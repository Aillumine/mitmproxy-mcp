"""MCP 工具调用分发表。"""

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def _call_sync(
    module_name: str, function_name: str, *args: Any, **kwargs: Any
) -> dict[str, Any]:
    function = getattr(importlib.import_module(module_name), function_name)
    return await asyncio.to_thread(function, *args, **kwargs)


async def _call_async(
    module_name: str, function_name: str, *args: Any, **kwargs: Any
) -> dict[str, Any]:
    function = getattr(importlib.import_module(module_name), function_name)
    return await function(*args, **kwargs)


async def _call_proxy_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync("mitm_proxy_mcp.tools.proxy_tools", "proxy_status")


async def _call_proxy_start(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.proxy_tools",
        "proxy_start",
        port=arguments.get("port", 8888),
        setup_proxy=arguments.get("setup_proxy", False),
    )


async def _call_proxy_stop(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.proxy_tools",
        "proxy_stop",
        port=arguments.get("port", 8888),
    )


async def _call_get_cert_info(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync("mitm_proxy_mcp.tools.proxy_tools", "get_cert_info")


async def _call_traffic_list(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.traffic_tools",
        "traffic_list",
        limit=arguments.get("limit", 10),
        offset=arguments.get("offset", 0),
        filter_domain=arguments.get("filter_domain"),
        filter_type=arguments.get("filter_type"),
        filter_status=arguments.get("filter_status"),
        filter_url=arguments.get("filter_url"),
        start_time=arguments.get("start_time"),
        end_time=arguments.get("end_time"),
        after_id=arguments.get("after_id"),
    )


async def _call_traffic_get_detail(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.traffic_tools",
        "traffic_get_detail",
        arguments["request_id"],
    )


async def _call_traffic_search(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.traffic_tools",
        "traffic_search",
        keyword=arguments["keyword"],
        search_in=arguments.get("search_in"),
        method=arguments.get("method"),
        domain=arguments.get("domain"),
        context_chars=arguments.get("context_chars", 150),
        limit=arguments.get("limit", 10),
    )


async def _call_traffic_read_body(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.traffic_tools",
        "traffic_read_body",
        request_id=arguments["request_id"],
        field=arguments.get("field", "response_body"),
        offset=arguments.get("offset", 0),
        length=arguments.get("length", 4000),
    )


async def _call_traffic_clear(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync("mitm_proxy_mcp.tools.traffic_tools", "traffic_clear")


async def _call_mock_add(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools",
        "mock_add",
        name=arguments["name"],
        url_pattern=arguments["url_pattern"],
        response_body=arguments["response_body"],
        method=arguments.get("method", ""),
        match_type=arguments.get("match_type", "contains"),
        status_code=arguments.get("status_code", 200),
        response_headers=arguments.get("response_headers"),
        delay_ms=arguments.get("delay_ms", 0),
        enabled=arguments.get("enabled", True),
    )


async def _call_mock_list(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools",
        "mock_list",
        enabled_only=arguments.get("enabled_only", False),
    )


async def _call_mock_update(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools",
        "mock_update",
        rule_id=arguments["rule_id"],
        name=arguments.get("name"),
        url_pattern=arguments.get("url_pattern"),
        response_body=arguments.get("response_body"),
        method=arguments.get("method"),
        match_type=arguments.get("match_type"),
        status_code=arguments.get("status_code"),
        response_headers=arguments.get("response_headers"),
        delay_ms=arguments.get("delay_ms"),
    )


async def _call_mock_delete(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools", "mock_delete", arguments["rule_id"]
    )


async def _call_mock_toggle(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools",
        "mock_toggle",
        rule_id=arguments["rule_id"],
        enabled=arguments.get("enabled"),
    )


async def _call_mock_clear(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync("mitm_proxy_mcp.tools.mock_tools", "mock_clear")


async def _call_mock_export(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync("mitm_proxy_mcp.tools.mock_tools", "mock_export")


async def _call_mock_import(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_sync(
        "mitm_proxy_mcp.tools.mock_tools",
        "mock_import",
        rules_json=arguments["rules_json"],
        merge=arguments.get("merge", False),
    )


async def _call_android_list_devices(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools", "android_list_devices"
    )


async def _call_android_get_device_info(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_get_device_info",
        arguments["serial"],
    )


async def _call_android_setup_proxy(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_setup_proxy",
        serial=arguments["serial"],
        proxy_host=arguments["proxy_host"],
        proxy_port=arguments.get("proxy_port", 8080),
    )


async def _call_android_clear_proxy(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_clear_proxy",
        arguments["serial"],
    )


async def _call_android_get_proxy(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_get_proxy",
        arguments["serial"],
    )


async def _call_android_cert_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_cert_status",
        arguments["serial"],
    )


async def _call_android_push_cert(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_push_cert",
        arguments["serial"],
    )


async def _call_android_inject_system_cert(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_inject_system_cert",
        arguments["serial"],
    )


async def _call_android_reverse_proxy(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_reverse_proxy",
        serial=arguments["serial"],
        port=arguments.get("port", 8888),
    )


async def _call_android_reverse_proxy_remove(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_reverse_proxy_remove",
        serial=arguments["serial"],
        port=arguments.get("port", 8888),
    )


async def _call_ios_list_devices(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async("mitm_proxy_mcp.tools.ios_tools", "ios_list_devices")


async def _call_ios_list_simulators(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async("mitm_proxy_mcp.tools.ios_tools", "ios_list_simulators")


async def _call_ios_list_real_devices(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async("mitm_proxy_mcp.tools.ios_tools", "ios_list_real_devices")


async def _call_ios_get_device_info(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.ios_tools", "ios_get_device_info", arguments["udid"]
    )


async def _call_ios_boot_simulator(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.ios_tools", "ios_boot_simulator", arguments["udid"]
    )


async def _call_ios_shutdown_simulator(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.ios_tools", "ios_shutdown_simulator", arguments["udid"]
    )


TOOL_HANDLERS: dict[str, Handler] = {
    "proxy_status": _call_proxy_status,
    "proxy_start": _call_proxy_start,
    "proxy_stop": _call_proxy_stop,
    "get_cert_info": _call_get_cert_info,
    "traffic_list": _call_traffic_list,
    "traffic_get_detail": _call_traffic_get_detail,
    "traffic_search": _call_traffic_search,
    "traffic_read_body": _call_traffic_read_body,
    "traffic_clear": _call_traffic_clear,
    "mock_add": _call_mock_add,
    "mock_list": _call_mock_list,
    "mock_update": _call_mock_update,
    "mock_delete": _call_mock_delete,
    "mock_toggle": _call_mock_toggle,
    "mock_clear": _call_mock_clear,
    "mock_export": _call_mock_export,
    "mock_import": _call_mock_import,
    "android_list_devices": _call_android_list_devices,
    "android_get_device_info": _call_android_get_device_info,
    "android_setup_proxy": _call_android_setup_proxy,
    "android_clear_proxy": _call_android_clear_proxy,
    "android_get_proxy": _call_android_get_proxy,
    "android_cert_status": _call_android_cert_status,
    "android_push_cert": _call_android_push_cert,
    "android_inject_system_cert": _call_android_inject_system_cert,
    "android_reverse_proxy": _call_android_reverse_proxy,
    "android_reverse_proxy_remove": _call_android_reverse_proxy_remove,
    "ios_list_devices": _call_ios_list_devices,
    "ios_list_simulators": _call_ios_list_simulators,
    "ios_list_real_devices": _call_ios_list_real_devices,
    "ios_get_device_info": _call_ios_get_device_info,
    "ios_boot_simulator": _call_ios_boot_simulator,
    "ios_shutdown_simulator": _call_ios_shutdown_simulator,
}


def list_tool_names() -> list[str]:
    """返回全部当前 MCP 工具名称。"""
    return list(TOOL_HANDLERS)


async def invoke_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用指定工具；未知工具名会抛出 KeyError。"""
    return await TOOL_HANDLERS[name](arguments)
