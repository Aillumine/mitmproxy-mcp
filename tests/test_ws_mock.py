import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mitmproxy.websocket import WebSocketMessage
from wsproto.frame_protocol import Opcode

from mitm_proxy_mcp.core.mock_store import MockStore
from mitm_proxy_mcp.tools import mock_tools


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(tmp_path / "traffic.db"))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "mock.db"))
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    return MockStore()


@pytest.fixture
def addon(store):
    from mitm_proxy_mcp.addon import traffic_addon

    return importlib.reload(traffic_addon)


def add(**kwargs):
    return mock_tools.mock_add("ws rule", "wss://example.com/ws", "mock", **kwargs)


def test_old_database_migrates_without_retyping_http_rules(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE mock_rules (
        id TEXT PRIMARY KEY, name TEXT, url_pattern TEXT, method TEXT,
        status_code INTEGER,
        response_headers TEXT, response_body TEXT, delay_ms INTEGER, enabled INTEGER,
        match_type TEXT, created_at REAL, updated_at REAL, hit_count INTEGER)""")
    conn.execute(
        "INSERT INTO mock_rules VALUES "
        "('old','old','/x','',200,'{}','ok',0,1,'contains',0,0,0)"
    )
    conn.commit()
    conn.close()
    rule = MockStore(path).get_by_id("old")
    assert rule.mock_type == "http"
    assert rule.ws_config == {}
    assert rule.response_body == "ok"


def test_ws_crud_export_import_roundtrip(store):
    config = {"protocol": "socketio", "messages": [{"body": '42["hi"]', "delay_ms": 5}]}
    result = add(mock_type="ws_server", ws_config=config)
    assert result["success"]
    rule_id = result["rule"]["id"]
    assert mock_tools.mock_list()["rules"][0]["ws_config"] == config
    assert mock_tools.mock_update(rule_id, ws_config={"protocol": "raw"})["success"]
    assert mock_tools.mock_toggle(rule_id, False)["success"]
    exported = mock_tools.mock_export()["rules"]
    assert mock_tools.mock_import(json.dumps(exported))["success"]
    rule = store.list_all()[0]
    assert rule.mock_type == "ws_server"
    assert rule.ws_config == {"protocol": "raw"}
    assert rule.enabled is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mock_type": "unknown"},
        {"mock_type": "ws_message", "ws_config": {"action": "unknown"}},
        {"mock_type": "ws_message", "ws_config": {"direction": "invalid"}},
        {
            "mock_type": "ws_message",
            "ws_config": {"message_match_type": "regex", "message_pattern": "["},
        },
        {"mock_type": "ws_server", "ws_config": {"messages": [{"body": 4}]}},
        {"mock_type": "ws_server", "ws_config": {"ping_interval_ms": 0}},
        {
            "mock_type": "ws_server",
            "ws_config": {"messages": [{"body": "x", "delay_ms": -1}]},
        },
        {"mock_type": "ws_server", "ws_config": []},
        {"delay_ms": -1},
    ],
)
def test_invalid_config_does_not_persist(store, kwargs):
    assert add(**kwargs)["success"] is False
    assert len(store) == 0


def test_invalid_update_and_import_preserve_saved_rules(store):
    result = add()
    rule_id = result["rule"]["id"]
    assert not mock_tools.mock_update(
        rule_id, mock_type="ws_server", ws_config={"protocol": "invalid"}
    )["success"]
    assert store.get_by_id(rule_id).mock_type == "http"
    assert not mock_tools.mock_import('{"rules":[{"mock_type":"invalid"}]}')["success"]
    assert store.get_by_id(rule_id) is not None


@pytest.mark.parametrize("action", ["replace", "drop", "reply"])
def test_message_actions_and_direction(addon, store, monkeypatch, action):
    result = add(
        mock_type="ws_message",
        ws_config={
            "direction": "client",
            "message_pattern": "^original$",
            "message_match_type": "regex",
            "action": action,
        },
    )
    commands = MagicMock()
    monkeypatch.setattr(
        addon.ctx, "master", SimpleNamespace(commands=commands), raising=False
    )
    flow = SimpleNamespace(request=SimpleNamespace(pretty_url="https://example.com/ws"))
    message = WebSocketMessage(Opcode.TEXT, True, b"original")
    asyncio.run(addon._apply_ws_mock(flow, message))
    assert message.content == (b"mock" if action == "replace" else b"original")
    assert message.dropped == (action != "replace")
    assert store.get_by_id(result["rule"]["id"]).hit_count == 1
    if action == "reply":
        commands.call.assert_called_once_with(
            "inject.websocket", flow, True, b"mock", True
        )
    else:
        commands.call.assert_not_called()
    opposite = WebSocketMessage(Opcode.TEXT, False, b"original")
    asyncio.run(addon._apply_ws_mock(flow, opposite))
    assert opposite.content == b"original"
    assert not opposite.dropped


def test_binary_and_injected_messages_bypass_rules(addon, store):
    result = add(mock_type="ws_message")
    flow = SimpleNamespace(request=SimpleNamespace(pretty_url="https://example.com/ws"))
    for message in [
        WebSocketMessage(Opcode.BINARY, False, b"original"),
        WebSocketMessage(Opcode.TEXT, False, b"original", injected=True),
    ]:
        asyncio.run(addon._apply_ws_mock(flow, message))
        assert message.content == b"original"
    assert store.get_by_id(result["rule"]["id"]).hit_count == 0


def test_http_rules_never_match_ws_message_rules(addon, store):
    add(mock_type="ws_message")
    assert addon.find_matching_mock("wss://example.com/ws", "GET") is None


@pytest.mark.asyncio
async def test_control_dispatch_roundtrips_ws_fields(store):
    from mitm_proxy_mcp.control.dispatch import invoke_tool

    created = await invoke_tool(
        "mock_add",
        {
            "name": "api ws",
            "url_pattern": "wss://example.com/ws",
            "response_body": "",
            "mock_type": "ws_server",
            "ws_config": {"protocol": "raw"},
        },
    )
    assert created["success"]
    rule_id = created["rule"]["id"]
    updated = await invoke_tool(
        "mock_update",
        {
            "rule_id": rule_id,
            "mock_type": "ws_message",
            "ws_config": {"direction": "client", "action": "drop"},
        },
    )
    assert updated["success"]
    assert updated["rule"]["mock_type"] == "ws_message"
    listed = await invoke_tool("mock_list", {})
    assert listed["rules"][0]["ws_config"] == {"direction": "client", "action": "drop"}


@pytest.mark.asyncio
async def test_mcp_schema_exposes_ws_fields():
    from mitm_proxy_mcp.server import list_tools

    tools = {tool.name: tool for tool in await list_tools()}
    for name in ("mock_add", "mock_update"):
        properties = tools[name].inputSchema["properties"]
        assert properties["mock_type"]["enum"] == ["http", "ws_message", "ws_server"]
        assert properties["ws_config"]["type"] == "object"


@pytest.mark.asyncio
async def test_offline_server_cleans_tasks_on_close():
    from mitm_proxy_mcp.addon.ws_server import MockWebSocketServer

    backend = MockWebSocketServer()
    started = asyncio.Event()

    async def serve(reader, writer):
        started.set()
        await backend.serve(reader, writer)

    backend.server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = backend.server.sockets[0].getsockname()[1]
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(started.wait(), 1)
    assert backend.tasks
    backend.close()
    await asyncio.gather(*backend.tasks, return_exceptions=True)
    await backend.server.wait_closed()
    writer.close()
    await writer.wait_closed()
    assert not backend.tasks
    assert not backend.pending
