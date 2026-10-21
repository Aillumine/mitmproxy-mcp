import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mitmproxy.websocket import WebSocketMessage

from mitm_proxy_mcp.addon.ws_faults import FaultSession
from mitm_proxy_mcp.core.ws_mock import validate_ws_config
from mitm_proxy_mcp.tools import mock_tools


@pytest.fixture
async def make_session():
    sessions = []

    def make(config, *, delay=100, socketio=False):
        flow = SimpleNamespace(
            request=SimpleNamespace(
                pretty_url=(
                    "https://example.com/socket.io/?EIO=4&transport=websocket"
                    if socketio
                    else "https://example.com/ws"
                )
            )
        )
        rule = {"id": "fault", "delay_ms": delay, "ws_config": config}
        session = FaultSession(flow, [rule], MagicMock(), MagicMock())
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.stop()
    await asyncio.gather(
        *(job for s in sessions for job in s.jobs), return_exceptions=True
    )


def message(body=b"original", *, client=False, binary=False):
    return WebSocketMessage(2 if binary else 1, client, body)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"action": "invalid"},
        {"action": "pause"},
        {"action": "pause", "duration_ms": 0},
        {"action": "delay", "after_messages": -1},
        {"action": "delay", "after_messages": True},
        {"action": "delay", "after_ms": -1},
        {"action": "delay", "direction": "up"},
        {"action": "delay", "trigger": "invalid"},
        {"action": "delay", "probability": 1.1},
        {"action": "delay", "probability": True},
        {"action": "delay", "probability": float("nan")},
        {"action": "delay", "preserve_heartbeat": "false"},
        {"action": "delay", "jitter_ms": -1},
        {"action": "delay", "seed": -1},
        {"action": "close", "close_code": 1006},
        {"action": "close", "close_code": 2000},
        {"action": "close", "close_code": True},
        {"action": "close", "close_reason": "中" * 42},
    ],
)
def test_fault_config_rejects_invalid_values(config):
    with pytest.raises(ValueError):
        validate_ws_config("ws_fault", config)


def test_fault_config_crud_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "mock.db"))
    config = {
        "action": "pause",
        "trigger": "messages",
        "after_messages": 5,
        "duration_ms": 1000,
        "direction": "both",
        "preserve_heartbeat": False,
    }
    created = mock_tools.mock_add(
        "中途暂停", "wss://example.com/ws", "", mock_type="ws_fault", ws_config=config
    )
    assert created["success"]
    assert mock_tools.mock_list()["rules"][0]["ws_config"] == config
    exported = mock_tools.mock_export()["rules"]
    assert mock_tools.mock_import(json.dumps(exported))["success"]
    assert mock_tools.mock_list()["rules"][0]["mock_type"] == "ws_fault"


@pytest.mark.asyncio
async def test_skip_count_and_direction_are_connection_local(make_session):
    config = {"action": "delay", "after_messages": 2}
    first = make_session(config)
    second = make_session(config)
    assert not first.apply(message(client=True))
    assert not first.apply(message())
    assert not first.apply(message())
    delayed = message()
    assert first.apply(delayed)
    assert delayed.dropped
    assert not second.apply(message())
    assert first.hit.call_count == 1
    assert second.hit.call_count == 0


@pytest.mark.asyncio
async def test_replay_is_not_delayed_again(make_session):
    session = make_session({"action": "delay"})
    replay = message()
    replay.fault_rule_ids = ("fault",)
    assert not session.apply(replay)
    assert not replay.dropped
    session.hit.assert_not_called()


@pytest.mark.asyncio
async def test_binary_payload_and_direction_survive_replay(make_session):
    session = make_session({"action": "delay", "direction": "client"}, delay=10)
    original = message(b"\x00\xffbytes", client=True, binary=True)
    assert session.apply(original)
    await asyncio.gather(*session.jobs)
    copied, ids = session.transport.replay.call_args.args
    assert copied.content == original.content
    assert copied.from_client is True
    assert copied.type == 2
    assert ids == {"fault"}


@pytest.mark.asyncio
async def test_jitter_is_bounded_and_seed_repeats(make_session):
    config = {"action": "delay", "jitter_ms": 30, "seed": 123}
    sessions = [make_session(config) for _ in range(2)]
    sampled = []
    for session in sessions:
        delays = []
        for _ in range(8):
            before = time.monotonic()
            session.apply(message())
            delay = session.queues[False][-1][1] - before
            assert 0.069 <= delay <= 0.131
            delays.append(round(delay, 3))
        sampled.append(delays)
    assert all(abs(a - b) <= 0.001 for a, b in zip(*sampled))


@pytest.mark.asyncio
async def test_probability_zero_never_delays_or_closes(make_session):
    session = make_session({"action": "delay", "probability": 0})
    assert not session.apply(message())
    closer = make_session(
        {"action": "abort", "trigger": "time", "after_ms": 0, "probability": 0}
    )
    await asyncio.gather(*closer.jobs)
    closer.transport.terminate.assert_not_called()
    closer.hit.assert_not_called()


@pytest.mark.asyncio
async def test_pause_fires_once_and_cleans_buffer(make_session):
    session = make_session({"action": "pause", "duration_ms": 20})
    assert session.apply(message(b"first"))
    assert session.apply(message(b"second"))
    await asyncio.gather(*session.jobs)
    assert [c.args[0].content for c in session.transport.replay.call_args_list] == [
        b"first",
        b"second",
    ]
    assert not session.apply(message(b"third"))
    session.hit.assert_called_once_with("fault")
    assert session.queued_bytes == 0


@pytest.mark.asyncio
async def test_stop_cancels_pending_timers_and_deliveries(make_session):
    session = make_session(
        {"action": "pause", "trigger": "time", "after_ms": 10, "duration_ms": 100}
    )
    await asyncio.sleep(0.02)
    assert session.apply(message())
    session.stop()
    await asyncio.gather(*session.jobs, return_exceptions=True)
    session.transport.replay.assert_not_called()
    assert session.queued_bytes == 0
    assert not any(session.queues.values())


@pytest.mark.asyncio
async def test_socketio_heartbeat_bypasses_backlog(make_session):
    session = make_session({"action": "pause", "duration_ms": 100}, socketio=True)
    assert session.apply(message(b'42["data"]'))
    assert not session.apply(message(b"2"))
    assert not session.apply(message(b'40{"sid":"x"}'))
    assert session.policies[0].count == 1


@pytest.mark.asyncio
async def test_plain_ws_numeric_payload_is_not_mistaken_for_heartbeat(make_session):
    session = make_session({"action": "delay"})
    assert session.apply(message(b"2"))


@pytest.mark.asyncio
async def test_queue_limit_closes_without_retaining_large_payload(make_session):
    session = make_session({"action": "pause", "duration_ms": 1000})
    assert session.apply(message(b"x" * (8 * 1024 * 1024 + 1)))
    assert session.closed
    assert session.queued_bytes == 0
    session.transport.terminate.assert_called_once_with(
        {
            "action": "close",
            "close_code": 1009,
            "close_reason": "mock queue limit",
        }
    )
