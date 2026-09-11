"""mitmproxy-start 与控制服务共享数据库路径的测试。"""

import os
from pathlib import Path

from mitm_proxy_mcp.cli.start import resolve_db_paths
from mitm_proxy_mcp.control.runtime import RuntimeInfo, write_runtime


def _runtime(pid: int, tmp_path: Path) -> RuntimeInfo:
    return RuntimeInfo(
        version=1,
        pid=pid,
        base_url="http://127.0.0.1:18765",
        token="secret",
        traffic_db=str(tmp_path / "control-traffic.db"),
        mock_db=str(tmp_path / "control-mock.db"),
        proxy_port=8888,
        capture_target="mac",
        started_at="2026-08-06T00:00:00Z",
    )


def test_uses_running_control_service_databases(monkeypatch, tmp_path):
    """控制服务在跑时，启动脚本复用它的 traffic/mock 数据库。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MITMPROXY_DB_PATH", raising=False)
    monkeypatch.delenv("MITMPROXY_MOCK_DB_PATH", raising=False)
    write_runtime(_runtime(os.getpid(), tmp_path))

    traffic_db, mock_db = resolve_db_paths()

    assert traffic_db == tmp_path / "control-traffic.db"
    assert mock_db == tmp_path / "control-mock.db"


def test_falls_back_when_runtime_process_is_dead(monkeypatch, tmp_path):
    """runtime.json 属于已退出的进程时回退到默认路径。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(tmp_path / "default-traffic.db"))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "default-mock.db"))
    write_runtime(_runtime(999_999_999, tmp_path))

    traffic_db, mock_db = resolve_db_paths()

    assert traffic_db == tmp_path / "default-traffic.db"
    assert mock_db == tmp_path / "default-mock.db"


def test_falls_back_without_runtime_file(monkeypatch, tmp_path):
    """没有 runtime.json 时使用默认路径。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(tmp_path / "default-traffic.db"))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "default-mock.db"))

    traffic_db, mock_db = resolve_db_paths()

    assert traffic_db == tmp_path / "default-traffic.db"
    assert mock_db == tmp_path / "default-mock.db"


def test_mitmdump_args_stream_large_bodies():
    from mitm_proxy_mcp.cli.start import mitmdump_args

    args = mitmdump_args(8888, "/tmp/addon.py")
    assert "stream_large_bodies=1m" in args
    assert "ssl_insecure=true" in args
    assert "-p" in args
    assert "8888" in args


def test_sigterm_runs_the_shutdown_path():
    """SIGTERM 必须抛 KeyboardInterrupt，否则 proxy_stop 会跳过退出清理。"""
    import os
    import signal

    from mitm_proxy_mcp.cli.start import install_sigterm_as_interrupt

    previous = signal.getsignal(signal.SIGTERM)
    try:
        install_sigterm_as_interrupt()
        cleaned = False
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            # 信号在字节码边界交付，给解释器一次执行机会。
            for _ in range(1000):
                pass
        except KeyboardInterrupt:
            cleaned = True
        assert cleaned, "SIGTERM 没有触发 KeyboardInterrupt，退出清理不会执行"
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_shutdown_restores_mac_proxy_on_any_exit(monkeypatch):
    """代理正常退出（不经 KeyboardInterrupt）也必须恢复 Mac 系统代理。"""
    from mitm_proxy_mcp.cli import start

    calls = []
    monkeypatch.setattr(start.time, "sleep", lambda _s: None)
    monkeypatch.setattr(start, "kill_port_process", lambda _port: False)
    monkeypatch.setattr(start, "check_port_available", lambda _port: True)
    monkeypatch.setattr(start, "disable_mac_proxy", lambda: calls.append("off") or True)

    start.shutdown_proxy(None, 8888, proxy_enabled=True)
    assert calls == ["off"]

    start.shutdown_proxy(None, 8888, proxy_enabled=False)
    assert calls == ["off"], "没设置过系统代理时不应去动它"
