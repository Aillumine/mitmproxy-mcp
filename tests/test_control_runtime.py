import os

from mitm_proxy_mcp.control.runtime import (
    RuntimeInfo,
    clear_runtime,
    generate_token,
    is_pid_alive,
    read_runtime,
    write_runtime,
)


def test_write_read_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    info = RuntimeInfo(
        version=1,
        pid=os.getpid(),
        base_url="http://127.0.0.1:18765",
        token=generate_token(),
        traffic_db=str(tmp_path / ".mitmscope/traffic.db"),
        mock_db=str(tmp_path / ".mitmscope/mock.db"),
        proxy_port=8888,
        capture_target="mac",
        started_at="2026-08-06T00:00:00Z",
    )
    write_runtime(info)
    loaded = read_runtime()
    assert loaded is not None
    assert loaded.token == info.token
    assert loaded.base_url == info.base_url
    clear_runtime()
    assert read_runtime() is None


def test_is_pid_alive_self():
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(999_999_999) is False
