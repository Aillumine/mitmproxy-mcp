import os
from pathlib import Path
from mitm_proxy_mcp.control.paths import mitmscope_dir, ensure_mitmscope_dir
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore
from mitm_proxy_mcp.core.mock_store import MockStore

def test_mitmscope_dir_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mitmscope_dir() == tmp_path / ".mitmscope"

def test_ensure_creates_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = ensure_mitmscope_dir()
    assert p.is_dir()

def test_store_default_path_reads_env(monkeypatch, tmp_path):
    traffic = tmp_path / "t.db"
    mock = tmp_path / "m.db"
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(traffic))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(mock))
    assert SQLiteTrafficStore.get_default_path() == traffic
    assert MockStore.get_default_path() == mock

def test_store_default_path_fallback_tmp(monkeypatch):
    monkeypatch.delenv("MITMPROXY_DB_PATH", raising=False)
    monkeypatch.delenv("MITMPROXY_MOCK_DB_PATH", raising=False)
    assert SQLiteTrafficStore.get_default_path() == Path("/tmp/mitmproxy-traffic.db")
    assert MockStore.get_default_path() == Path("/tmp/mitmproxy-mock.db")

def test_store_init_uses_env_default(monkeypatch, tmp_path):
    traffic = tmp_path / "t.db"
    mock = tmp_path / "m.db"
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(traffic))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(mock))
    assert SQLiteTrafficStore().db_path == traffic
    assert MockStore().db_path == mock
