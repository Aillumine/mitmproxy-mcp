"""addon 弱网限速钩子。"""

import asyncio
import importlib
from unittest.mock import MagicMock
from urllib.parse import urlparse

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
    host = urlparse(url).hostname or url.split(":")[0]
    flow.request.host = host
    flow.request.pretty_host = host
    flow.request.raw_content = b""
    flow.request.content = b""
    flow.request.headers = {}
    flow.response.headers = {"content-type": "application/json"}
    flow.response.raw_content = b""
    flow.metadata = {}
    flow.client_conn.peername = (client_ip, 51234)
    return flow


@pytest.fixture
def clock(monkeypatch):
    """A hand-cranked monotonic clock: burst detection must not race real time.

    手动推进的单调时钟：突发判定不能受真实耗时影响。
    """
    state = {"now": 1000.0}
    monkeypatch.setattr(
        "mitm_proxy_mcp.addon.traffic_addon.time.monotonic", lambda: state["now"]
    )
    return state


@pytest.fixture
def sleeps(monkeypatch, clock):
    """Capture awaited delays; the hooks pace with asyncio.sleep, never time.sleep.

    A throttling sleep really does move the clock forward, so the fake advances
    it too — without that, burst detection looks fine in tests while the injected
    delay pushes every real frame past the gap on the wire.

    记录 await 出去的延迟；钩子用 asyncio.sleep 限速，绝不能用 time.sleep。
    限速的 sleep 在真实世界里会推进时间，所以假 sleep 也要推进时钟——不然
    突发判定在测试里看着正常，真机上却因为自己注入的延迟把每一帧都撑成新突发。
    """
    recorded: list[float] = []

    async def fake_sleep(seconds):
        recorded.append(seconds)
        clock["now"] += seconds

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


class _Msg:
    def __init__(self, from_client: bool, content: bytes):
        self.from_client = from_client
        self.content = content


def _ws_flow(content: bytes, *, from_client: bool, client_ip: str = "192.168.1.50"):
    flow = _flow(
        client_ip=client_ip,
        url="https://example.com/socket.io/?EIO=4&transport=websocket",
    )
    flow.request.host = "example.com"
    flow.metadata = {}
    flow.websocket.messages = [_Msg(from_client, content)]
    return flow


def test_ws_server_frame_pays_rtt_then_download_bandwidth(throttled, sleeps, clock):
    # 4g: 20ms RTT, 4000 kbps -> 500000 B/s, 5000 bytes -> 0.01s
    run(throttled.websocket_message(_ws_flow(b"x" * 5000, from_client=False)))
    assert [s for s in sleeps if s > 0] == [0.02, 0.01]


def test_ws_client_frame_uses_upload_bandwidth(throttled, sleeps, clock):
    # 4g: 3000 kbps -> 375000 B/s, 3750 bytes -> 0.01s
    run(throttled.websocket_message(_ws_flow(b"x" * 3750, from_client=True)))
    assert [s for s in sleeps if s > 0] == [0.02, 0.01]


def test_ws_burst_frames_pay_the_rtt_once(throttled, sleeps, clock):
    """流式回复是小帧连发：管道化到达，不该每帧都吃一次完整往返。"""
    flow = _ws_flow(b"x" * 500, from_client=False)
    for _ in range(3):
        run(throttled.websocket_message(flow))
        clock["now"] += 0.001  # 服务端连发：下一帧早就在管道里了
    assert [s for s in sleeps if s > 0].count(0.02) == 1


def test_ws_frame_after_idle_gap_pays_the_rtt_again(throttled, sleeps, clock):
    """方向静默超过一个 RTT 后，下一帧是新一轮往返的首帧。"""
    flow = _ws_flow(b"x" * 500, from_client=False)
    run(throttled.websocket_message(flow))
    clock["now"] += 5.0
    run(throttled.websocket_message(flow))
    assert [s for s in sleeps if s > 0].count(0.02) == 2


def test_ws_directions_track_their_own_bursts(throttled, sleeps, clock):
    """上下行各算各的突发：下行连发不该吃掉上行的首帧延迟。"""
    flow = _ws_flow(b"x" * 500, from_client=False)
    run(throttled.websocket_message(flow))
    clock["now"] += 0.001
    flow.websocket.messages = [_Msg(True, b"x" * 500)]
    run(throttled.websocket_message(flow))
    assert [s for s in sleeps if s > 0].count(0.02) == 2


def test_ws_frame_from_the_mac_is_never_throttled(throttled, sleeps, clock):
    flow = _ws_flow(b"x" * 5000, from_client=False, client_ip="127.0.0.1")
    run(throttled.websocket_message(flow))
    assert sleeps == []


def test_ws_frame_unthrottled_when_profile_is_off(addon, sleeps, clock):
    addon._throttle_cache["mtime"] = None
    run(addon.websocket_message(_ws_flow(b"x" * 5000, from_client=False)))
    assert sleeps == []


def test_ws_bandwidth_uses_the_full_frame_not_the_captured_slice(
    throttled, sleeps, clock
):
    """限速按线上实际字节算，不能按抓包截断后的长度算。"""
    size = throttled.CAPTURE_BODY_LIMIT + 500_000
    run(throttled.websocket_message(_ws_flow(b"x" * size, from_client=False)))
    assert any(abs(s - size / 500_000) < 1e-9 for s in sleeps)


@pytest.fixture
def stalled(addon, monkeypatch):
    from mitm_proxy_mcp.core.throttle import config_for_profile

    monkeypatch.setattr(addon, "find_matching_mock", lambda *_: None)
    monkeypatch.setattr(
        addon,
        "get_throttle_config",
        lambda: config_for_profile("stall").with_scope(heartbeat_exempt=False),
    )
    return addon


def test_ws_frames_25s_apart_are_separate_bursts(stalled, sleeps, clock):
    """真机实测：25s 的间隔不能因为 RTT 有 130s 就被算成同一突发。"""
    flow = _ws_flow(b"2", from_client=False)
    run(stalled.websocket_message(flow))
    clock["now"] += 25.0
    run(stalled.websocket_message(flow))
    assert len([s for s in sleeps if s == 130.0]) == 2


def test_ws_streamed_chunks_still_pipeline_under_stall(stalled, sleeps, clock):
    """同一次回复里连发的 chunk 仍然只在首帧付一次，压超时靠的就是那一次。"""
    flow = _ws_flow(b"x" * 50, from_client=False)
    run(stalled.websocket_message(flow))
    clock["now"] += 0.05
    run(stalled.websocket_message(flow))
    assert len([s for s in sleeps if s == 130.0]) == 1


def _scoped(addon, monkeypatch, **scope):
    from mitm_proxy_mcp.core.throttle import config_for_profile

    config = config_for_profile("2g").with_scope(**scope)
    monkeypatch.setattr(addon, "find_matching_mock", lambda *_: None)
    monkeypatch.setattr(addon, "get_throttle_config", lambda: config)
    return addon


def test_empty_whitelist_still_throttles_everything(addon, monkeypatch, sleeps):
    """向后兼容：没配白名单就和以前一样，外部流量全限。"""
    scoped = _scoped(addon, monkeypatch, domains=[])
    run(scoped.request(_flow(url="https://connectivitycheck.gstatic.com/generate_204")))
    assert any(abs(s - 0.3) < 1e-9 for s in sleeps)


def test_whitelisted_host_is_throttled(addon, monkeypatch, sleeps):
    scoped = _scoped(addon, monkeypatch, domains=["*.flowgpt.com"])
    flow = _flow(url="https://staging-ws-flow-dev.flowgpt.com/api/chat")
    flow.request.host = "staging-ws-flow-dev.flowgpt.com"
    run(scoped.request(flow))
    assert any(abs(s - 0.3) < 1e-9 for s in sleeps)


def test_system_connectivity_check_is_spared(addon, monkeypatch, sleeps):
    """真机实测：拖死系统联网检测会让 Android 判定断网，业务请求根本发不出来。"""
    scoped = _scoped(addon, monkeypatch, domains=["*.flowgpt.com"])
    for host in (
        "connectivitycheck.gstatic.com",
        "captive.samsungconnectivity.com",
        "play.googleapis.com",
        "graph.facebook.com",
    ):
        flow = _flow(url=f"http://{host}/generate_204")
        flow.request.host = host
        run(scoped.request(flow))
    assert sleeps == []


def test_connect_tunnel_host_is_read_from_the_request(addon, monkeypatch):
    """CONNECT 流没有 URL 路径，域名只能从 request.host / SNI 取。"""
    from types import SimpleNamespace

    scoped = _scoped(addon, monkeypatch, domains=["*.flowgpt.com"])
    connect = _flow(url="staging-ws-flow-dev.flowgpt.com:443")
    connect.request.method = "CONNECT"
    connect.request.host = "staging-ws-flow-dev.flowgpt.com"
    assert scoped.throttle_applies(connect) is not None

    spared = _flow(url="captive.samsungconnectivity.com:443")
    spared.request.method = "CONNECT"
    spared.request.host = "captive.samsungconnectivity.com"
    assert scoped.throttle_applies(spared) is None

    # TLS 握手失败时只有 client_conn.sni，没有 request。
    sni_only = SimpleNamespace(
        client_conn=SimpleNamespace(
            peername=("192.168.1.50", 51234), sni="staging-ws-flow-dev.flowgpt.com"
        )
    )
    assert scoped.throttle_applies(sni_only) is not None


def test_socketio_heartbeat_frames_are_exempt(throttled, sleeps, clock):
    """心跳被限速会让 socket.io 判定连接死亡，压出来的是断连而不是超时。"""
    for payload in (b"2", b"3"):
        run(throttled.websocket_message(_ws_flow(payload, from_client=False)))
    assert sleeps == []


def test_business_frames_are_still_throttled(throttled, sleeps, clock):
    scoped_flow = _ws_flow(b'42["chat",{"text":"hi"}]', from_client=True)
    run(throttled.websocket_message(scoped_flow))
    assert any(abs(s - 0.02) < 1e-9 for s in sleeps)


def test_heartbeat_exemption_can_be_turned_off(addon, monkeypatch, sleeps, clock):
    """关掉豁免就回到压断连的行为，作为显式选项保留。"""
    scoped = _scoped(addon, monkeypatch, heartbeat_exempt=False)
    run(scoped.websocket_message(_ws_flow(b"2", from_client=False)))
    assert any(abs(s - 0.3) < 1e-9 for s in sleeps)
