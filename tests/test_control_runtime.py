import os
import stat

from mitm_proxy_mcp.control.paths import runtime_json_path
from mitm_proxy_mcp.control.runtime import (
    RuntimeInfo,
    clear_runtime,
    generate_token,
    is_pid_alive,
    read_runtime,
    update_capture_target,
    write_runtime,
)


def _info(tmp_path) -> RuntimeInfo:
    return RuntimeInfo(
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


def test_write_read_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    info = _info(tmp_path)
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


def test_runtime_file_is_only_readable_by_owner(monkeypatch, tmp_path):
    """runtime.json 含 Bearer token，权限必须是 0600。"""
    monkeypatch.setenv("HOME", str(tmp_path))

    target = write_runtime(_info(tmp_path))

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_rewrite_tightens_permissions_of_existing_file(monkeypatch, tmp_path):
    """已存在的宽松权限文件会在重写时被收紧。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = runtime_json_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o644)

    write_runtime(_info(tmp_path))

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_update_capture_target_rewrites_runtime(monkeypatch, tmp_path):
    """capture_target 变更会写回 runtime.json 且不破坏其他字段。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    info = _info(tmp_path)
    write_runtime(info)

    assert update_capture_target("device") is True

    loaded = read_runtime()
    assert loaded is not None
    assert loaded.capture_target == "device"
    assert loaded.token == info.token
    assert stat.S_IMODE(runtime_json_path().stat().st_mode) == 0o600


def test_update_capture_target_without_runtime_file(monkeypatch, tmp_path):
    """没有 runtime.json 时更新返回 False 且不创建文件。"""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert update_capture_target("device") is False
    assert not runtime_json_path().exists()
