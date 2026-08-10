"""本地控制服务的 FastAPI 应用。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
) -> FastAPI:
    """创建使用 Bearer token 认证的本地控制服务。"""
    security = HTTPBearer(auto_error=False)

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
            )
        if credentials.credentials != token:
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

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": "1.0.0",
            "proxy_running": bool(proxy_status().get("running", False)),
            "pid": os.getpid(),
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

    return app
