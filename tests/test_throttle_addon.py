"""addon 弱网限速钩子。"""

import importlib
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def addon(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(tmp_path / "traffic.db"))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "mock.db"))
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))

    from mitm_proxy_mcp.addon import traffic_addon

    importlib.reload(traffic_addon)
    return traffic_addon


def test_get_throttle_config_reads_profile(addon, tmp_path):
    from mitm_proxy_mcp.core.throttle import set_profile

    set_profile("2g", tmp_path / "throttle.json")
    addon._throttle_cache["mtime"] = None
    config = addon.get_throttle_config()
    assert config.profile == "2g"
    assert config.enabled is True


def test_request_applies_latency(addon, monkeypatch):
    from mitm_proxy_mcp.core.throttle import ThrottleConfig

    sleeps: list[float] = []
    monkeypatch.setattr(
        "mitm_proxy_mcp.core.throttle.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        addon,
        "get_throttle_config",
        lambda: ThrottleConfig(
            profile="4g", latency_ms=20, download_kbps=4000, upload_kbps=3000
        ),
    )

    flow = MagicMock()
    flow.request.method = "GET"
    flow.request.pretty_url = "https://example.com/api"
    flow.request.raw_content = b""
    flow.request.content = b""
    flow.request.headers = {}
    monkeypatch.setattr(addon, "find_matching_mock", lambda *_: None)

    addon.request(flow)
    assert any(abs(s - 0.02) < 1e-9 for s in sleeps)


def test_throttled_stream_paces_chunks(addon, monkeypatch):
    from mitm_proxy_mcp.core.throttle import ThrottleConfig

    sleeps: list[float] = []
    monkeypatch.setattr(
        "mitm_proxy_mcp.core.throttle.time.sleep",
        lambda s: sleeps.append(s),
    )
    # 8 bytes / 8000 Bps = 0.001s
    stream = addon._throttled_stream(8000.0)
    list(stream([b"12345678"]))
    assert len(sleeps) == 1
    assert abs(sleeps[0] - 0.001) < 1e-9

    flow = MagicMock()
    flow.request.method = "GET"
    flow.request.pretty_url = "https://cdn.example.com/a.png"
    flow.request.headers = {}
    flow.response.headers = {"content-type": "image/png"}
    flow.metadata = {}
    monkeypatch.setattr(
        addon,
        "get_throttle_config",
        lambda: ThrottleConfig(
            profile="3g", latency_ms=100, download_kbps=750, upload_kbps=250
        ),
    )
    addon.responseheaders(flow)
    assert callable(flow.response.stream)
    assert flow.metadata.get("throttle_streamed") is True
