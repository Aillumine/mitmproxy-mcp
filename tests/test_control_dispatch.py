"""控制服务工具分发表测试。"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_list_contains_all_current_tools():
    """分发表包含当前 MCP 暴露的全部工具。"""
    from mitm_proxy_mcp.control.dispatch import list_tool_names

    tool_names = list_tool_names()

    assert "proxy_status" in tool_names
    assert len(tool_names) >= 33


@pytest.mark.asyncio
async def test_unknown_tool_raises_unknown_tool_error():
    """未知工具名抛出专用异常。"""
    from mitm_proxy_mcp.control.dispatch import UnknownToolError, invoke_tool

    with pytest.raises(UnknownToolError) as error:
        await invoke_tool("no_such_tool", {})

    assert error.value.name == "no_such_tool"


@pytest.mark.asyncio
async def test_missing_required_argument_is_not_unknown_tool():
    """缺少必填参数抛 KeyError，不能被误判成未知工具。"""
    from mitm_proxy_mcp.control.dispatch import UnknownToolError, invoke_tool

    with pytest.raises(KeyError) as error:
        await invoke_tool("traffic_get_detail", {})

    assert not isinstance(error.value, UnknownToolError)
    assert error.value.args[0] == "request_id"


@pytest.mark.asyncio
async def test_invoke_proxy_status_uses_current_module_function(monkeypatch):
    """同步工具在调用时导入，支持替换模块函数。"""
    from mitm_proxy_mcp.control import dispatch

    monkeypatch.setattr(
        "mitm_proxy_mcp.tools.proxy_tools.proxy_status",
        lambda: {"running": False, "message": "ok"},
    )

    result = await dispatch.invoke_tool("proxy_status", {})

    assert result["message"] == "ok"


@pytest.mark.asyncio
async def test_invoke_proxy_start_uses_server_defaults(monkeypatch):
    """代理启动的缺省参数与 MCP 服务一致。"""
    from mitm_proxy_mcp.control import dispatch

    captured: dict[str, object] = {}

    def fake_proxy_start(port: int, setup_proxy: bool, open_ui: bool = False):
        captured.update(port=port, setup_proxy=setup_proxy, open_ui=open_ui)
        return {"success": True}

    monkeypatch.setattr(
        "mitm_proxy_mcp.tools.proxy_tools.proxy_start", fake_proxy_start
    )

    await dispatch.invoke_tool("proxy_start", {})

    assert captured == {"port": 8888, "setup_proxy": False, "open_ui": False}


@pytest.mark.asyncio
async def test_invoke_proxy_start_forwards_open_ui(monkeypatch):
    from mitm_proxy_mcp.control import dispatch

    captured: dict[str, object] = {}

    def fake_proxy_start(port: int, setup_proxy: bool, open_ui: bool = False):
        captured.update(port=port, setup_proxy=setup_proxy, open_ui=open_ui)
        return {"success": True}

    monkeypatch.setattr(
        "mitm_proxy_mcp.tools.proxy_tools.proxy_start", fake_proxy_start
    )

    await dispatch.invoke_tool("proxy_start", {"open_ui": True})

    assert captured["open_ui"] is True


@pytest.mark.asyncio
async def test_invoke_android_tool_awaits_current_module_function(monkeypatch):
    """异步工具直接等待模块中的当前函数。"""
    from mitm_proxy_mcp.control import dispatch

    fake_list_devices = AsyncMock(return_value={"success": True, "devices": []})
    monkeypatch.setattr(
        "mitm_proxy_mcp.tools.android_tools.android_list_devices",
        fake_list_devices,
    )

    result = await dispatch.invoke_tool("android_list_devices", {})

    assert result["success"] is True
    fake_list_devices.assert_awaited_once_with()
