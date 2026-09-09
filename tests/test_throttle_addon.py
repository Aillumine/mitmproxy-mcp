"""addon 弱网限速钩子。"""

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest


def run(coro):
    """addon hooks are coroutines now; drive them to completion in sync tests.

    addon 钩子已改为协程，同步测试里把它跑完。
    """
    return asyncio.run(coro)


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


def _flow(*, client_ip: str = "192.168.1.50", url: str = "https://example.com/api"):
    flow = MagicMock()
    flow.request.method = "GET"
    flow.request.pretty_url = url
    flow.request.raw_content = b""
    flow.request.content = b""
    flow.request.headers = {}
    flow.response.headers = {"content-type": "application/json"}
    flow.response.raw_content = b""
    flow.metadata = {}
    flow.client_conn.peername = (client_ip, 51234)
    return flow


@pytest.fixture
def sleeps(monkeypatch):
    """Capture awaited delays; the hooks pace with asyncio.sleep, never time.sleep.

    记录 await 出去的延迟；钩子用 asyncio.sleep 限速，绝不能用 time.sleep。
    """
    recorded: list[float] = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr("mitm_proxy_mcp.addon.traffic_addon.asyncio.sleep", fake_sleep)
    return recorded


@pytest.fixture
def throttled(addon, monkeypatch):
    from mitm_proxy_mcp.core.throttle import ThrottleConfig

    monkeypatch.setattr(addon, "find_matching_mock", lambda *_: None)
    monkeypatch.setattr(
        addon,
        "get_throttle_config",
        lambda: ThrottleConfig(
            profile="4g", latency_ms=20, download_kbps=4000, upload_kbps=3000
        ),
    )
    return addon


def test_request_applies_latency_to_device(throttled, sleeps):
    run(throttled.request(_flow()))
    assert any(abs(s - 0.02) < 1e-9 for s in sleeps)


def test_throttle_never_touches_the_mac(throttled, sleeps):
    """本机（回环）流量不限速——弱网只能作用在连过来的手机上。"""
    for ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        run(throttled.request(_flow(client_ip=ip)))
    assert sleeps == []


def test_unknown_peer_is_treated_as_local(throttled, sleeps):
    """取不到对端地址时按本机处理，宁可漏限也不拖慢 Mac。"""
    flow = _flow()
    flow.client_conn.peername = None
    run(throttled.request(flow))
    assert sleeps == []


def test_device_client_detection(addon):
    assert addon.is_device_client(_flow(client_ip="192.168.1.50")) is True
    assert addon.is_device_client(_flow(client_ip="127.0.0.1")) is False
    assert addon.is_device_client(_flow(client_ip="::1")) is False


def test_throttled_flow_is_buffered_not_streamed(throttled):
    """限速的设备流不能走 stream：stream 回调是同步的，没法在里面 await。"""
    flow = _flow()
    flow.response.stream = False
    run(throttled.responseheaders(flow))
    assert flow.response.stream is False
    assert flow.metadata.get("capture_body") is None


def test_download_paced_by_forwarded_bytes(throttled, sleeps):
    flow = _flow()
    flow.response.raw_content = b"x" * 5000
    # 5000 bytes / (4000 kbps -> 500000 Bps) = 0.01s
    run(throttled._throttle_download(flow))
    assert any(abs(s - 0.01) < 1e-9 for s in sleeps)


class _ExplodingRequest:
    """读 body 就炸，用来证明没人碰它。"""

    method = "GET"
    pretty_url = "https://example.com/api"
    headers: dict = {}

    @property
    def raw_content(self):
        raise AssertionError("限速关闭时不该读取请求体")

    @property
    def content(self):
        raise AssertionError("限速关闭时不该读取请求体")


def test_no_throttle_means_no_body_decode(addon, monkeypatch):
    """限速关闭时不该为了限速去解码请求体——之前每个请求都白解一次。"""
    flow = _flow()
    flow.request = _ExplodingRequest()
    monkeypatch.setattr(addon, "find_matching_mock", lambda *_: None)
    run(addon.request(flow))
