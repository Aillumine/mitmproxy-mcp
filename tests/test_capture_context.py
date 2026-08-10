import pytest

from mitm_proxy_mcp.control.capture_context import (
    get_capture_target,
    resolve_setup_proxy,
    set_capture_target,
)
from mitm_proxy_mcp.control.runtime import RuntimeInfo, read_runtime, write_runtime


@pytest.fixture(autouse=True)
def reset_capture_target(monkeypatch, tmp_path):
    # 隔离 HOME，避免用例把真实 ~/.mitmscope/runtime.json 改掉。
    monkeypatch.setenv("HOME", str(tmp_path))
    set_capture_target("mac")
    yield
    set_capture_target("mac")


def test_default_mac():
    set_capture_target("mac")
    assert get_capture_target() == "mac"


def test_set_capture_target_persists_to_runtime_json(tmp_path):
    """切换抓包目标会同步写回 runtime.json。"""
    write_runtime(
        RuntimeInfo(
            version=1,
            pid=1234,
            base_url="http://127.0.0.1:18765",
            token="secret",
            traffic_db=str(tmp_path / "traffic.db"),
            mock_db=str(tmp_path / "mock.db"),
            proxy_port=8888,
            capture_target="mac",
            started_at="2026-08-06T00:00:00Z",
        )
    )

    set_capture_target("device")

    loaded = read_runtime()
    assert loaded is not None
    assert loaded.capture_target == "device"


def test_set_capture_target_survives_broken_runtime_json():
    """runtime.json 损坏时内存状态仍然更新，不抛异常。"""
    from mitm_proxy_mcp.control.paths import runtime_json_path

    target = runtime_json_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not json", encoding="utf-8")

    set_capture_target("device")

    assert get_capture_target() == "device"


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
