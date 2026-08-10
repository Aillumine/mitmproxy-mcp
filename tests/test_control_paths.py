import stat
from pathlib import Path

from mitm_proxy_mcp.control.paths import ensure_mitmscope_dir, mitmscope_dir
from mitm_proxy_mcp.core.mock_store import MockStore
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore


def test_mitmscope_dir_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mitmscope_dir() == tmp_path / ".mitmscope"

def test_ensure_creates_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = ensure_mitmscope_dir()
    assert p.is_dir()

def test_ensure_dir_is_private(monkeypatch, tmp_path):
    """目录内含 token 与抓包数据，权限必须是 0700。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert stat.S_IMODE(ensure_mitmscope_dir().stat().st_mode) == 0o700

def test_ensure_dir_tightens_existing_permissions(monkeypatch, tmp_path):
    """已存在的宽松目录会被收紧到 0700。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    existing = tmp_path / ".mitmscope"
    existing.mkdir()
    existing.chmod(0o755)
    assert stat.S_IMODE(ensure_mitmscope_dir().stat().st_mode) == 0o700

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
