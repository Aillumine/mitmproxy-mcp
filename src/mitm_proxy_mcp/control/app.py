"""本地控制服务的 FastAPI 应用。"""

from __future__ import annotations

import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from mitm_proxy_mcp.control import webui_dir
from mitm_proxy_mcp.control.capture_context import get_capture_target
from mitm_proxy_mcp.control.dispatch import (
    UnknownToolError,
    list_tool_names,
)
from mitm_proxy_mcp.control.dispatch import (
    invoke_tool as default_invoke_tool,
)
from mitm_proxy_mcp.control.runtime import (
    RuntimeInfo,
)
from mitm_proxy_mcp.control.runtime import (
    read_runtime as default_read_runtime,
)
from mitm_proxy_mcp.tools.proxy_tools import proxy_status

ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
RuntimeReader = Callable[[], RuntimeInfo | None]
ToolLister = Callable[[], list[str]]
ProxyStatus = Callable[[], dict[str, Any]]
CaptureTargetGetter = Callable[[], str]
ListenIpGetter = Callable[[], str]

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
COOKIE_NAME = "mitm_ui"

_LISTEN_IP: str | None = None


def default_listen_ip() -> str:
    """本机局域网 IP，供设备填写代理；失败则 127.0.0.1。"""
    global _LISTEN_IP
    if _LISTEN_IP:
        return _LISTEN_IP
    ip = "127.0.0.1"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        found = sock.getsockname()[0]
        sock.close()
        if found and found != "0.0.0.0":
            ip = found
    except OSError:
        pass
    _LISTEN_IP = ip
    return ip


def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _error_message(error: Exception) -> str:
    """把工具异常转成可读的错误信息。"""
    if isinstance(error, KeyError):
        return f"missing required argument: {error.args[0]}"
    return str(error) or error.__class__.__name__


def create_app(
    token: str,
    *,
    get_capture_target: CaptureTargetGetter = get_capture_target,
    invoke_tool: ToolInvoker = default_invoke_tool,
    list_tools: ToolLister = list_tool_names,
    proxy_status: ProxyStatus = proxy_status,
    read_runtime: RuntimeReader = default_read_runtime,
    get_listen_ip: ListenIpGetter = default_listen_ip,
) -> FastAPI:
    """创建使用 Bearer token 认证的本地控制服务。"""
    security = HTTPBearer(auto_error=False)

    def require_token(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
        mitm_ui: str | None = Cookie(default=None),
    ) -> None:
        presented = None
        if credentials is not None and credentials.scheme.lower() == "bearer":
            presented = credentials.credentials
        elif mitm_ui:
            presented = mitm_ui
        if presented != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
            )

    app = FastAPI(
        title="mitmproxy-control",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/v1/ui/session")
    def ui_session(request: Request, response: Response) -> dict[str, bool]:
        if _client_host(request) not in LOOPBACK_HOSTS:
            raise HTTPException(status_code=403, detail="loopback only")
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=86400,
            secure=False,
        )
        return {"ok": True}

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": "1.0.0",
            "proxy_running": bool(proxy_status().get("running", False)),
            "pid": os.getpid(),
            "proxy_host": get_listen_ip(),
        }

    @app.get("/v1/runtime", dependencies=[Depends(require_token)])
    def runtime() -> dict[str, Any]:
        info = read_runtime()
        if info is None:
            return {"capture_target": get_capture_target()}
        data = asdict(info)
        data.pop("token", None)
        # 进程内的抓包目标才是权威值，runtime.json 可能落后于最近一次切换。
        data["capture_target"] = get_capture_target()
        return data

    @app.get("/v1/tools", dependencies=[Depends(require_token)])
    def tools() -> dict[str, list[str]]:
        return {"tools": list_tools()}

    @app.get("/v1/events", dependencies=[Depends(require_token)])
    def events() -> None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="events are not implemented",
        )

    @app.post(
        "/v1/tools/{tool_name}",
        dependencies=[Depends(require_token)],
        response_model=None,
    )
    async def call_tool(
        tool_name: str, arguments: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any] | JSONResponse:
        try:
            return await invoke_tool(tool_name, arguments)
        except UnknownToolError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown tool: {tool_name}",
            ) from error
        except Exception as error:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "message": _error_message(error)},
            )

    ui = webui_dir.webui_path()
    if ui is None:
        @app.get("/")
        def no_ui() -> PlainTextResponse:
            return PlainTextResponse(
                "Web UI not built. In repo: cd web && npm install && npm run build",
                status_code=503,
            )
    else:
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(ui / "index.html")

        assets = ui / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", response_model=None)
        def spa(path: str) -> FileResponse | PlainTextResponse:
            if path.startswith("v1/") or path in {"docs", "redoc", "openapi.json"}:
                return PlainTextResponse("not found", status_code=404)
            return FileResponse(ui / "index.html")

    return app
