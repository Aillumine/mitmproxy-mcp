"""弱网预设与配置读写。"""

from pathlib import Path

import pytest

from mitm_proxy_mcp.core import throttle


def test_profiles_include_4g_3g_2g_off():
    assert set(throttle.PROFILES) == {"off", "4g", "3g", "2g"}
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
