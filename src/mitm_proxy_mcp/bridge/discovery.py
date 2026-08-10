"""发现或启动本地控制服务。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

from mitm_proxy_mcp.bridge.client import ControlClient
from mitm_proxy_mcp.control.paths import control_log_path
from mitm_proxy_mcp.control.runtime import read_runtime

POLL_INTERVAL_S = 0.1


def _get_project_root() -> Path | None:
    """从当前模块位置向上查找项目根目录。"""
    current = Path(__file__).resolve()
    while current != current.parent:
        if (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    return None


def _launch_control() -> subprocess.Popen[bytes] | None:
    """在后台启动控制服务，并将输出追加到控制日志。"""
    uv_command = shutil.which("uv")
    if uv_command is None:
        return None

    log_path = control_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    popen_kwargs: dict[str, object] = {
        "stdout": None,
        "stderr": subprocess.STDOUT,
        "start_new_session": True,
    }
    project_root = _get_project_root()
    if project_root is not None:
        popen_kwargs["cwd"] = project_root

    with log_path.open("a", encoding="utf-8") as log_file:
        popen_kwargs["stdout"] = log_file
        return subprocess.Popen(
            [uv_command, "run", "mitmproxy-control"],
            **popen_kwargs,
        )


async def _healthy_client() -> ControlClient | None:
    info = read_runtime()
    if info is None:
        return None
    client = ControlClient.from_runtime(info)
    return client if await client.health() else None


async def resolve_backend(
    *, launch: bool = True, timeout_s: float = 5.0
) -> ControlClient | None:
    """返回可用控制服务客户端；必要时尝试拉起服务。"""
    client = await _healthy_client()
    if client is not None or not launch:
        return client

    if _launch_control() is None:
        return None

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        client = await _healthy_client()
        if client is not None:
            return client
    return None
