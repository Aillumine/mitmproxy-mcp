"""控制服务 HTTP 客户端。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from mitm_proxy_mcp.control.runtime import RuntimeInfo


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
            async with httpx.AsyncClient(headers=self._headers, timeout=1.0) as client:
                response = await client.get(f"{self.base_url}/v1/health")
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用控制服务中的指定工具。"""
        async with httpx.AsyncClient(headers=self._headers, timeout=5.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/tools/{name}",
                json=arguments,
            )
        response.raise_for_status()
        return response.json()
