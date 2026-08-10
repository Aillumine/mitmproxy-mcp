"""runtime.json 读写 helpers。"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from mitm_proxy_mcp.control.paths import MITMSCOPE_DIR_MODE, runtime_json_path

# runtime.json 保存控制服务 Bearer token，只允许当前用户读写。
RUNTIME_FILE_MODE = 0o600


@dataclass
class RuntimeInfo:
    version: int
    pid: int
    base_url: str
    token: str
    traffic_db: str
    mock_db: str
    proxy_port: int
    capture_target: str
    started_at: str


def generate_token() -> str:
    """生成控制服务 Bearer token。"""
    return secrets.token_urlsafe(32)


def _resolve_path(path: Path | None) -> Path:
    return path if path is not None else runtime_json_path()


def write_runtime(info: RuntimeInfo, path: Path | None = None) -> Path:
    """以 0600 权限写入 runtime.json 并返回实际路径。"""
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=MITMSCOPE_DIR_MODE)
    payload = json.dumps(asdict(info), indent=2) + "\n"
    # 用 os.open 创建，避免 token 在 chmod 之前存在可被他人读取的时间窗口。
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, RUNTIME_FILE_MODE
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    # 文件已存在时 os.open 不会应用 mode，需要显式收紧权限。
    target.chmod(RUNTIME_FILE_MODE)
    return target


def read_runtime(path: Path | None = None) -> RuntimeInfo | None:
    """读取 runtime.json；文件不存在时返回 None。"""
    target = _resolve_path(path)
    if not target.is_file():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    return RuntimeInfo(**data)


def update_capture_target(target: str, path: Path | None = None) -> bool:
    """把 capture_target 写回 runtime.json；文件不存在时返回 False。"""
    info = read_runtime(path)
    if info is None:
        return False
    if info.capture_target != target:
        info.capture_target = target
        write_runtime(info, path)
    return True


def clear_runtime(path: Path | None = None) -> None:
    """删除 runtime.json（不存在时无操作）。"""
    target = _resolve_path(path)
    if target.is_file():
        target.unlink()


def is_pid_alive(pid: int) -> bool:
    """检查进程是否仍在运行。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
