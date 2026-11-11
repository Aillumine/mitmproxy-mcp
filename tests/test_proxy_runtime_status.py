import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mitm_proxy_mcp.tools import proxy_tools as proxy


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "PID_FILE", tmp_path / "proxy.pid")
    from types import SimpleNamespace

    monkeypatch.setattr(
        proxy, "read_runtime", lambda: SimpleNamespace(proxy_port=free_port())
    )
    monkeypatch.setattr(
        proxy.SQLiteTrafficStore,
        "get_default_path",
        staticmethod(lambda: tmp_path / "traffic.db"),
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_history_is_not_liveness(isolated):
    with patch.object(proxy.SQLiteTrafficStore, "exists", return_value=True):
        assert proxy.proxy_status()["running"] is False


def test_unrelated_listener_and_reused_pid(isolated):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        proxy.PID_FILE.write_text(str(os.getpid()))
        assert proxy._find_proxy_process_by_port(port) is None
        assert proxy.proxy_stop(port)["success"] is False


def test_real_proxy_lifecycle_without_database(isolated):
    port = free_port()
    executable = str(Path(sys.executable).parent / "mitmdump")
    process = subprocess.Popen(
        [executable, "--listen-host", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        with patch.object(proxy, "read_runtime") as runtime:
            runtime.return_value.proxy_port = port
            with patch.object(proxy.SQLiteTrafficStore, "exists", return_value=False):
                assert proxy.proxy_status()["running"] is True
        process.terminate()
        process.wait(timeout=10)
        proxy.PID_FILE.write_text(str(process.pid))
        with patch.object(proxy, "read_runtime") as runtime:
            runtime.return_value.proxy_port = port
            with patch.object(proxy.SQLiteTrafficStore, "exists", return_value=True):
                assert proxy.proxy_status()["running"] is False
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_stale_pid_without_listener(isolated):
    proxy.PID_FILE.write_text("2147483647")
    assert proxy.proxy_status()["running"] is False


def test_live_pid_without_listener(isolated):
    proxy.PID_FILE.write_text(str(os.getpid()))
    assert proxy.proxy_status()["running"] is False


def test_stop_preserves_explicit_port(isolated, monkeypatch):
    monkeypatch.setattr(proxy, "_configured_proxy_port", lambda: 9999)
    ports = []
    monkeypatch.setattr(
        proxy, "_find_proxy_process_by_port", lambda port: ports.append(port)
    )
    assert proxy.proxy_stop(8888)["success"] is False
    assert ports == [8888]


def test_status_uses_saved_start_port(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "PID_FILE", tmp_path / "proxy.pid")
    monkeypatch.setattr(proxy, "read_runtime", lambda: None)
    monkeypatch.setattr(proxy, "_find_proxy_process_by_port", lambda port: 123)
    proxy._save_proxy_port(9999)
    assert proxy._configured_proxy_port() == 9999


def test_stale_saved_port_falls_back_to_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(proxy, "PID_FILE", tmp_path / "proxy.pid")
    monkeypatch.setattr(proxy, "read_runtime", lambda: SimpleNamespace(proxy_port=8888))
    monkeypatch.setattr(proxy, "_find_proxy_process_by_port", lambda port: None)
    proxy._save_proxy_port(9999)
    assert proxy._configured_proxy_port() == 8888
