"""弱网预设与配置读写。"""

from pathlib import Path

import pytest

from mitm_proxy_mcp.core import throttle


def test_profiles_include_4g_3g_2g_off_and_stall():
    assert set(throttle.PROFILES) == {"off", "4g", "3g", "2g", "stall"}
    assert throttle.config_for_profile("4g").latency_ms == 20
    assert throttle.config_for_profile("3g").download_kbps == 750
    assert throttle.config_for_profile("2g").upload_kbps == 20
    assert throttle.config_for_profile("off").enabled is False


def test_set_profile_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "throttle.json"
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(path))
    config = throttle.set_profile("3g")
    assert config.profile == "3g"
    assert path.is_file()
    loaded = throttle.read_config()
    assert loaded.profile == "3g"
    assert loaded.latency_ms == 100


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="未知弱网档位"):
        throttle.set_profile("5g")


def test_throttle_tools_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "throttle.json"
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(path))
    from mitm_proxy_mcp.tools import throttle_tools

    off = throttle_tools.throttle_get()
    assert off["success"] is True
    assert off["profile"] == "off"

    result = throttle_tools.throttle_set("4g")
    assert result["success"] is True
    assert result["profile"] == "4g"
    assert "立即生效" in result["message"]

    current = throttle_tools.throttle_get()
    assert current["profile"] == "4g"
    assert current["download_kbps"] == 4000


def test_stall_profile_outlasts_client_receive_timeouts():
    """压超时档位：单次往返必须超过客户端最长的 120s 流式接收超时。"""
    config = throttle.config_for_profile("stall")
    assert config.enabled is True
    assert config.latency_ms > 120_000
    assert 0 < config.download_bps < 1000
    assert 0 < config.upload_bps < 1000


def test_stall_profile_round_trips_across_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """档位写在共享配置文件里，代理进程读回来要原样还原。"""
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    throttle.set_profile("stall")
    loaded = throttle.read_config()
    assert loaded.profile == "stall"
    assert loaded.latency_ms == throttle.PROFILES["stall"]["latency_ms"]


def test_stall_profile_is_listed():
    assert "stall" in {item["profile"] for item in throttle.list_profiles()}


def test_unknown_profile_message_lists_stall():
    with pytest.raises(ValueError, match="stall"):
        throttle.normalize_profile("5g")


def test_throttle_set_accepts_stall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    from mitm_proxy_mcp.tools import throttle_tools

    result = throttle_tools.throttle_set("stall")
    assert result["success"] is True
    assert result["profile"] == "stall"


def test_mcp_tool_enum_covers_every_profile():
    """MCP 工具的 enum 少一个档位，客户端就根本调不到它。"""
    import asyncio

    from mitm_proxy_mcp.server import list_tools

    tool = next(t for t in asyncio.run(list_tools()) if t.name == "throttle_set")
    enum = tool.inputSchema["properties"]["profile"]["enum"]
    assert set(enum) == set(throttle.PROFILES)


def test_defaults_throttle_everything_and_spare_heartbeats():
    """向后兼容：不配白名单就限全部流量；心跳豁免默认开。"""
    config = throttle.config_for_profile("2g")
    assert config.domains == ()
    assert config.heartbeat_exempt is True


def test_domains_and_heartbeat_flag_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    throttle.write_config(
        throttle.config_for_profile("2g").with_scope(
            domains=["*.flowgpt.com", "API.Example.com"], heartbeat_exempt=False
        )
    )
    loaded = throttle.read_config()
    assert loaded.domains == ("*.flowgpt.com", "api.example.com")
    assert loaded.heartbeat_exempt is False


def test_switching_profile_keeps_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """白名单是调试现场的设置，不该被切档位冲掉。"""
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    throttle.write_config(
        throttle.config_for_profile("2g").with_scope(domains=["*.flowgpt.com"])
    )
    switched = throttle.set_profile("stall")
    assert switched.profile == "stall"
    assert switched.domains == ("*.flowgpt.com",)
    assert throttle.read_config().domains == ("*.flowgpt.com",)


def test_latency_override_survives_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """压 30s/60s 的路径不必每次等满 130s。"""
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    config = throttle.set_profile("stall", latency_ms=35000)
    assert config.latency_ms == 35000
    loaded = throttle.read_config()
    assert loaded.profile == "stall"
    assert loaded.latency_ms == 35000
    assert loaded.download_kbps == throttle.PROFILES["stall"]["download_kbps"]


def test_host_matches_supports_wildcards():
    patterns = ("*.flowgpt.com", "api.example.com")
    assert throttle.host_matches("staging-ws-flow-dev.flowgpt.com", patterns) is True
    assert throttle.host_matches("API.example.com", patterns) is True
    assert throttle.host_matches("connectivitycheck.gstatic.com", patterns) is False
    assert throttle.host_matches("flowgpt.com", patterns) is False
    # 空白名单 = 不过滤
    assert throttle.host_matches("anything.example", ()) is True


def test_to_dict_is_json_friendly():
    data = throttle.config_for_profile("2g").with_scope(domains=["a.com"]).to_dict()
    assert data["domains"] == ["a.com"]
    assert data["heartbeat_exempt"] is True


def test_throttle_set_configures_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MITMPROXY_THROTTLE_PATH", str(tmp_path / "throttle.json"))
    from mitm_proxy_mcp.tools import throttle_tools

    result = throttle_tools.throttle_set(
        "stall", domains=["*.flowgpt.com"], heartbeat_exempt=False, latency_ms=35000
    )
    assert result["success"] is True
    assert result["domains"] == ["*.flowgpt.com"]
    assert result["heartbeat_exempt"] is False
    assert result["latency_ms"] == 35000
    assert throttle_tools.throttle_get()["domains"] == ["*.flowgpt.com"]
