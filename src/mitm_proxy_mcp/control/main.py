"""mitmproxy-control 命令行入口。"""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime

import uvicorn

from mitm_proxy_mcp.control.app import create_app
from mitm_proxy_mcp.control.capture_context import get_capture_target
from mitm_proxy_mcp.control.paths import (
    ensure_mitmscope_dir,
    mock_db_path,
    throttle_json_path,
    traffic_db_path,
)
from mitm_proxy_mcp.control.runtime import (
    RuntimeInfo,
    clear_runtime,
    generate_token,
    read_runtime,
    write_runtime,
)
from mitm_proxy_mcp.tools.proxy_tools import proxy_start, proxy_stop

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18765
DEFAULT_PROXY_PORT = 8888


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


def cleanup(proxy_port: int | None = None) -> None:
    """删除本进程写入的 runtime.json，并停止它记录的代理端口。

    runtime.json 属于另一个存活的控制服务时不做任何事，避免误杀它的代理。
    """
    info = read_runtime()
    if info is None or info.pid != os.getpid():
        return
    port = info.proxy_port if proxy_port is None else proxy_port
    clear_runtime()
    try:
        proxy_stop(port=port)
    except Exception:
        pass


def register_shutdown_handlers(
    *, proxy_port: int | None = None, shutdown: Callable[[], None] | None = None
) -> None:
    """在解释器退出和终止信号时执行清理。"""
    finalizer = shutdown or (lambda: cleanup(proxy_port))
    atexit.register(finalizer)

    def handle_signal(signum: int, frame: object) -> None:
        finalizer()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 mitmproxy-control 命令行参数。默认不打开浏览器、不自动起代理。"""
    parser = argparse.ArgumentParser(description="mitmproxy-control 本地控制服务")
    parser.add_argument(
        "--open",
        action="store_true",
        help="启动后在浏览器中打开本机网页",
    )
    parser.add_argument(
        "--proxy",
        action="store_true",
        help="同时后台启动抓包代理（不改 Mac 系统代理），并打开本机网页",
    )
    return parser.parse_args(argv)


def schedule_proxy_start(*, delay_s: float = 0.6) -> None:
    """等控制服务开始监听后再拉起抓包代理，避免阻塞 Uvicorn 启动。"""

    def boot() -> None:
        time.sleep(delay_s)
        proxy_start(port=DEFAULT_PROXY_PORT, setup_proxy=False, open_ui=False)

    threading.Thread(target=boot, daemon=True, name="mitm-proxy-boot").start()


def main(argv: list[str] | None = None) -> None:
    """初始化本地状态、启动 Uvicorn，并在退出时清理。"""
    args = parse_args(argv)
    ensure_mitmscope_dir()
    traffic_db = traffic_db_path()
    mock_db = mock_db_path()
    os.environ["MITMPROXY_DB_PATH"] = str(traffic_db)
    os.environ["MITMPROXY_MOCK_DB_PATH"] = str(mock_db)
    os.environ["MITMPROXY_THROTTLE_PATH"] = str(throttle_json_path())

    port = select_port()
    token = generate_token()
    capture_target = get_capture_target()
    base_url = f"http://{CONTROL_HOST}:{port}"
    write_runtime(
        RuntimeInfo(
            version=1,
            pid=os.getpid(),
            base_url=base_url,
            token=token,
            traffic_db=str(traffic_db),
            mock_db=str(mock_db),
            proxy_port=DEFAULT_PROXY_PORT,
            capture_target=capture_target,
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    register_shutdown_handlers()
    print(f"UI: http://{CONTROL_HOST}:{port}/", flush=True)
    if args.open or args.proxy:
        webbrowser.open(base_url)
    if args.proxy:
        schedule_proxy_start()
    try:
        uvicorn.run(create_app(token), host=CONTROL_HOST, port=port)
    finally:
        cleanup()


def main_proxy(argv: list[str] | None = None) -> None:
    """`uv run proxy` / 全局 alias：等同 `mitmproxy-control --proxy`。"""
    extra = list(argv) if argv is not None else sys.argv[1:]
    if "--proxy" not in extra:
        extra = ["--proxy", *extra]
    main(extra)


if __name__ == "__main__":
    main()
