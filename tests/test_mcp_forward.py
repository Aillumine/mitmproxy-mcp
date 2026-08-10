"""MCP 服务到控制服务的转发测试。"""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_backend():
    """每个用例前后都清空模块级后端缓存，避免测试互相污染。"""
    from mitm_proxy_mcp import server

    server._backend = None
    server._backend_resolved = False
    yield
    server._backend = None
    server._backend_resolved = False


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


@pytest.mark.asyncio
async def test_dead_backend_falls_back_and_is_reresolved(monkeypatch):
    """控制服务中途死亡时，本次调用回退本地分发并清空缓存的后端。"""
    from mitm_proxy_mcp import server

    class DeadBackend:
        async def call_tool(self, name, arguments):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(server, "_backend", DeadBackend())
    monkeypatch.setattr(server, "_backend_resolved", True)
    invoke = AsyncMock(return_value={"ok": True, "local": True})
    monkeypatch.setattr(server, "invoke_tool", invoke)

    result = await server.dispatch_tool("proxy_status", {})

    assert result == {"ok": True, "local": True}
    invoke.assert_awaited_once_with("proxy_status", {})
    assert server._backend is None
    assert server._backend_resolved is False


@pytest.mark.asyncio
async def test_backend_is_reresolved_on_next_call_after_failure(monkeypatch):
    """后端失效后，下一次调用会重新解析控制服务。"""
    from mitm_proxy_mcp import server

    class FlakyBackend:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadTimeout("timed out")
            return {"forwarded": True}

    backend = FlakyBackend()
    monkeypatch.setattr(server, "_backend", backend)
    monkeypatch.setattr(server, "_backend_resolved", True)
    monkeypatch.setattr(server, "invoke_tool", AsyncMock(return_value={"local": True}))
    monkeypatch.setattr(server, "resolve_backend", AsyncMock(return_value=backend))

    assert await server.dispatch_tool("proxy_status", {}) == {"local": True}
    assert await server.dispatch_tool("proxy_status", {}) == {"forwarded": True}


@pytest.mark.asyncio
async def test_tool_failure_on_live_backend_is_not_retried_locally(monkeypatch):
    """控制服务返回工具错误体时不回退，避免同一个工具被执行两次。"""
    from mitm_proxy_mcp import server

    class FailingToolBackend:
        async def call_tool(self, name, arguments):
            return {"success": False, "message": "database unavailable"}

    monkeypatch.setattr(server, "_backend", FailingToolBackend())
    monkeypatch.setattr(server, "_backend_resolved", True)
    invoke = AsyncMock(return_value={"local": True})
    monkeypatch.setattr(server, "invoke_tool", invoke)

    result = await server.dispatch_tool("traffic_clear", {})

    assert result == {"success": False, "message": "database unavailable"}
    invoke.assert_not_awaited()
    assert server._backend is not None


@pytest.mark.asyncio
async def test_concurrent_first_calls_resolve_backend_once(monkeypatch):
    """并发首次调用只解析（并只拉起）一次控制服务。"""
    from mitm_proxy_mcp import server

    resolutions = 0

    class Fake:
        async def call_tool(self, name, arguments):
            return {"forwarded": True}

    async def slow_resolve():
        nonlocal resolutions
        resolutions += 1
        await asyncio.sleep(0.05)
        return Fake()

    monkeypatch.setattr(server, "resolve_backend", slow_resolve)

    results = await asyncio.gather(
        *(server.dispatch_tool("proxy_status", {}) for _ in range(5))
    )

    assert resolutions == 1
    assert all(result == {"forwarded": True} for result in results)
