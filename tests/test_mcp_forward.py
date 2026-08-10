"""MCP 服务到控制服务的转发测试。"""

import json
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_call_tool_forwards_when_backend_set(monkeypatch):
    """存在控制服务后端时，工具调用会被转发。"""
    from mitm_proxy_mcp import server

    class Fake:
        async def call_tool(self, name, arguments):
            return {"forwarded": True, "name": name}

    monkeypatch.setattr(server, "_backend", Fake())

    result = await server.call_tool("proxy_status", {})
    data = json.loads(result[0].text)

    assert data["forwarded"] is True
    assert data["name"] == "proxy_status"


@pytest.mark.asyncio
async def test_call_tool_fallback_invoke(monkeypatch):
    """未发现控制服务时，工具调用回退到本地分发器。"""
    from mitm_proxy_mcp import server

    monkeypatch.setattr(server, "_backend", None)
    monkeypatch.setattr(server, "resolve_backend", AsyncMock(return_value=None))
    invoke = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(server, "invoke_tool", invoke)

    result = await server.call_tool("proxy_status", {})
    data = json.loads(result[0].text)

    assert data == {"ok": True}
    invoke.assert_awaited_once_with("proxy_status", {})
