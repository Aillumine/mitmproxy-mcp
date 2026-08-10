"""mitmproxy-control 命令行入口。"""

from __future__ import annotations

import atexit
import os
import signal
import socket
from collections.abc import Callable
from datetime import UTC, datetime

import uvicorn

from mitm_proxy_mcp.control.app import create_app
from mitm_proxy_mcp.control.capture_context import get_capture_target
from mitm_proxy_mcp.control.paths import (
    ensure_mitmscope_dir,
    mock_db_path,
    traffic_db_path,
)
from mitm_proxy_mcp.control.runtime import (
    RuntimeInfo,
    clear_runtime,
    generate_token,
    write_runtime,
)
from mitm_proxy_mcp.tools.proxy_tools import proxy_stop

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18765


def select_port() -> int:
    """优先使用固定端口；被占用时选择空闲本地端口。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.bind((CONTROL_HOST, CONTROL_PORT))
        except OSError:
            sock.bind((CONTROL_HOST, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def cleanup(proxy_port: int = 8888) -> None:
    """删除运行时文件并尽力停止代理。"""
    clear_runtime()
    try:
        proxy_stop(port=proxy_port)
    except Exception:
        pass


def register_shutdown_handlers(
    *, proxy_port: int, shutdown: Callable[[], None] | None = None
) -> None:
    """在解释器退出和终止信号时执行清理。"""
    finalizer = shutdown or (lambda: cleanup(proxy_port))
    atexit.register(finalizer)

    def handle_signal(signum: int, frame: object) -> None:
        finalizer()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main() -> None:
    """初始化本地状态、启动 Uvicorn，并在退出时清理。"""
    ensure_mitmscope_dir()
    traffic_db = traffic_db_path()
    mock_db = mock_db_path()
    os.environ["MITMPROXY_DB_PATH"] = str(traffic_db)
    os.environ["MITMPROXY_MOCK_DB_PATH"] = str(mock_db)

    port = select_port()
    token = generate_token()
    capture_target = get_capture_target()
    write_runtime(
        RuntimeInfo(
            version=1,
            pid=os.getpid(),
            base_url=f"http://{CONTROL_HOST}:{port}",
            token=token,
            traffic_db=str(traffic_db),
            mock_db=str(mock_db),
            proxy_port=8888,
            capture_target=capture_target,
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    register_shutdown_handlers(proxy_port=8888)
    try:
        uvicorn.run(create_app(token), host=CONTROL_HOST, port=port)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
