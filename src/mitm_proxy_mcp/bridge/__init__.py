"""控制服务桥接接口。"""

from mitm_proxy_mcp.bridge.client import ControlClient
from mitm_proxy_mcp.bridge.discovery import resolve_backend

__all__ = ["ControlClient", "resolve_backend"]
