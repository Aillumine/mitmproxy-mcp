"""控制服务 HTTP 客户端。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from mitm_proxy_mcp.control.runtime import RuntimeInfo

HEALTH_TIMEOUT_S = 1.0
CONNECT_TIMEOUT_S = 5.0
# proxy_start 会等待代理就绪（约 10s），ios_boot_simulator 更久，
# 因此读超时必须远大于连接超时。
READ_TIMEOUT_S = 120.0


def call_timeout() -> httpx.Timeout:
    """返回工具调用使用的超时配置。"""
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        read=READ_TIMEOUT_S,
        write=CONNECT_TIMEOUT_S,
        pool=CONNECT_TIMEOUT_S,
    )


def _error_payload(response: httpx.Response) -> dict[str, Any] | None:
    """把控制服务返回的错误响应转成标准错误体；无法解析时返回 None。

    工具在控制服务里执行失败是业务结果而不是链路故障，必须原样带回，
    否则调用方会误判服务已死而在本地重跑一次同样的工具。
    """
    if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    if "success" in body:
        return body
    detail = body.get("detail")
    if detail is None:
        return None
    return {"success": False, "message": str(detail)}


@dataclass(frozen=True)
class ControlClient:
    """调用本地 mitmproxy-control 服务。"""

    base_url: str
    token: str

    @classmethod
    def from_runtime(cls, info: RuntimeInfo) -> ControlClient:
        """从运行时信息创建客户端。"""
        return cls(base_url=info.base_url.rstrip("/"), token=info.token)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def health(self) -> bool:
        """检查控制服务是否可用。"""
        try:
            async with httpx.AsyncClient(
                headers=self._headers, timeout=HEALTH_TIMEOUT_S
            ) as client:
                response = await client.get(f"{self.base_url}/v1/health")
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用控制服务中的指定工具。"""
        async with httpx.AsyncClient(
            headers=self._headers, timeout=call_timeout()
        ) as client:
            response = await client.post(
                f"{self.base_url}/v1/tools/{name}",
                json=arguments,
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            payload = _error_payload(response)
            if payload is not None:
                return payload
        response.raise_for_status()
        return response.json()
