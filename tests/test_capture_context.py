import pytest

from mitm_proxy_mcp.control.capture_context import (
    get_capture_target,
    resolve_setup_proxy,
    set_capture_target,
)


@pytest.fixture(autouse=True)
def reset_capture_target():
    set_capture_target("mac")
    yield
    set_capture_target("mac")


def test_default_mac():
    set_capture_target("mac")
    assert get_capture_target() == "mac"


def test_device_forbids_setup_proxy():
    set_capture_target("device")
    allowed, reason = resolve_setup_proxy(True)
    assert allowed is False
    assert reason and "device" in reason


def test_simulator_allows_explicit_true():
    set_capture_target("simulator")
    allowed, reason = resolve_setup_proxy(True)
    assert allowed is True
    assert reason is None


def test_mac_allows_explicit_true():
    set_capture_target("mac")
    allowed, reason = resolve_setup_proxy(True)
    assert allowed is True
    assert reason is None


def test_proxy_start_strips_setup_proxy_for_device(monkeypatch):
    from mitm_proxy_mcp.tools import proxy_tools

    class UnusedPortSocket:
        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            return 1

        def close(self):
            pass

    set_capture_target("device")
    monkeypatch.setattr(proxy_tools, "_read_pid", lambda: None)
    monkeypatch.setattr(proxy_tools, "_find_proxy_process_by_port", lambda port: None)
    monkeypatch.setattr(proxy_tools.socket, "socket", lambda *args: UnusedPortSocket())
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        raise RuntimeError("stop")

    monkeypatch.setattr(proxy_tools.subprocess, "Popen", fake_popen)
    result = proxy_tools.proxy_start(setup_proxy=True)

    assert "--setup-proxy" not in captured.get("cmd", [])
    assert result["setup_proxy"] is False
    assert "device" in result.get("message", "").lower() or result.get("setup_proxy_blocked")
