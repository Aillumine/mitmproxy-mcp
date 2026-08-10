"""runtime.json 读写 helpers。"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from mitm_proxy_mcp.control.paths import runtime_json_path


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
    """写入 runtime.json 并返回实际路径。"""
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(asdict(info), indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def read_runtime(path: Path | None = None) -> RuntimeInfo | None:
    """读取 runtime.json；文件不存在时返回 None。"""
    target = _resolve_path(path)
    if not target.is_file():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    return RuntimeInfo(**data)


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
