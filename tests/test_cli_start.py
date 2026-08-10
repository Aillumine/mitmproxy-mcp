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
