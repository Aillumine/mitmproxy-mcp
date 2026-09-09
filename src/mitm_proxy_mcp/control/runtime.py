"""runtime.json 读写 helpers。"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
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


def is_zombie(pid: int) -> bool:
    """True when the process has exited but its parent has not reaped it yet.

    进程已退出、父进程还没回收时为 True。
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    state = result.stdout.strip()
    return bool(state) and state[0] == "Z"


def is_pid_alive(pid: int) -> bool:
    """Whether the process is still running — a zombie does not count.

    signal 0 succeeds against a zombie, which is how a stopped proxy kept
    reporting itself as running and wedged both proxy_start and proxy_stop.

    进程是否仍在运行——僵尸不算。
    signal 0 对僵尸进程是成功的，正因如此已停止的代理会一直被判为「还在跑」，
    把 proxy_start 和 proxy_stop 双双卡死。
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not is_zombie(pid)


def reap(pid: int) -> None:
    """Collect an exited child so it stops lingering as a zombie.

    Silently ignored when the process is not our child — the caller may have
    found the pid via lsof rather than having spawned it.

    回收已退出的子进程，避免它一直挂着变僵尸。
    若该进程不是本进程的子进程则静默忽略——调用方可能是通过 lsof 找到的 pid，
    并非自己启动的。
    """
    if pid <= 0:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
