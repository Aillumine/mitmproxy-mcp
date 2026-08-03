"""
MCP 服务入口

基于 MCP 协议的 Android 无头抓包服务。
流量数据通过 SQLite 与启动脚本共享。
"""

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import (
    android_cert_status,
    android_clear_proxy,
    android_get_device_info,
    android_get_proxy,
    android_inject_system_cert,
    android_list_devices,
    android_push_cert,
    android_setup_proxy,
    get_cert_info,
    ios_boot_simulator,
    ios_get_device_info,
    ios_list_devices,
    ios_list_real_devices,
    ios_list_simulators,
    ios_shutdown_simulator,
    mock_add,
    mock_clear,
    mock_delete,
    mock_export,
    mock_import,
    mock_list,
    mock_toggle,
    mock_update,
    proxy_start,
    proxy_status,
    proxy_stop,
    traffic_clear,
    traffic_get_detail,
    traffic_list,
    traffic_read_body,
    traffic_search,
)

# 创建 MCP 服务器
server = Server("mitmproxy-mcp")


# ============== 工具定义 ==============

@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    return [
        # 代理状态工具
        Tool(
            name="proxy_status",
            description="获取代理服务器状态。注意：需要先使用 proxy_start 或运行 'uv run mitmproxy-start' 启动代理。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="proxy_start",
            description="启动代理服务（后台运行）。可以直接启动代理，无需手动在终端运行命令。",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "代理端口，默认 8888",
                        "default": 8888,
                    },
                    "setup_proxy": {
                        "type": "boolean",
                        "description": "是否自动设置 Mac Wi-Fi 系统代理，默认 False",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="proxy_stop",
            description="停止代理服务。停止正在运行的代理进程。",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "代理端口，默认 8888（用于查找进程）",
                        "default": 8888,
                    },
                },
            },
        ),
        Tool(
            name="get_cert_info",
            description="获取 CA 证书信息和安装指南。抓取 HTTPS 流量需要在设备上安装此证书。",
            inputSchema={"type": "object", "properties": {}},
        ),
        # 流量工具
        Tool(
            name="traffic_list",
            description="列出捕获的 HTTP/HTTPS 流量。默认返回最近 10 条，支持分页和筛选。",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回数量限制，默认 10，最大 10",
                        "default": 10,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "跳过前 N 条记录，用于分页（如 offset=10 查看第 11-20 条）",
                        "default": 0,
                    },
                    "filter_domain": {
                        "type": "string",
                        "description": "按域名筛选，支持通配符（如 *.example.com）",
                    },
                    "filter_type": {
                        "type": "string",
                        "description": "按资源类型筛选（XHR, Document, Image, Script, Stylesheet, Font, Media, Other）",
                    },
                    "filter_status": {
                        "type": "string",
                        "description": "按状态码筛选（如 200, 4xx, 500-599）",
                    },
                    "filter_url": {
                        "type": "string",
                        "description": "按 URL 筛选，支持正则表达式",
                    },
                    "start_time": {
                        "type": "number",
                        "description": "开始时间（Unix 时间戳），筛选该时间之后的请求",
                    },
                    "end_time": {
                        "type": "number",
                        "description": "结束时间（Unix 时间戳），筛选该时间之前的请求",
                    },
                },
            },
        ),
        Tool(
            name="traffic_get_detail",
            description="获取单个请求的元数据（请求头、响应头、参数等）。注意：不包含请求体和响应体内容，使用 traffic_read_body 读取。",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "请求 ID（从 traffic_list 获取）",
                    },
                },
                "required": ["request_id"],
            },
        ),
        Tool(
            name="traffic_search",
            description="搜索流量内容。可搜索 URL、请求头、请求体、响应头、响应体。返回匹配的片段而非完整内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "search_in": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索范围：url, request_headers, request_body, response_headers, response_body, all（默认）",
                    },
                    "method": {
                        "type": "string",
                        "description": "限定 HTTP 方法（GET/POST）",
                    },
                    "domain": {
                        "type": "string",
                        "description": "限定域名（支持通配符 %）",
                    },
                    "context_chars": {
                        "type": "integer",
                        "description": "返回匹配内容前后字符数，默认 150",
                        "default": 150,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几条匹配，默认 10",
                        "default": 10,
                    },
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="traffic_read_body",
            description="分片读取请求体或响应体。用于查看大内容，支持分页读取。",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "请求 ID",
                    },
                    "field": {
                        "type": "string",
                        "description": "读取字段：request_body 或 response_body（默认）",
                        "default": "response_body",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始位置，默认 0",
                        "default": 0,
                    },
                    "length": {
                        "type": "integer",
                        "description": "读取长度，默认 4000 字符",
                        "default": 4000,
                    },
                },
                "required": ["request_id"],
            },
        ),
        Tool(
            name="traffic_clear",
            description="清空所有捕获的流量",
            inputSchema={"type": "object", "properties": {}},
        ),
        # Mock 工具
        Tool(
            name="mock_add",
            description="添加 mock 规则。匹配的请求将直接返回预设响应，不转发到真实服务器。规则实时生效（代理运行时无需重启）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "规则名称（便于识别）",
                    },
                    "url_pattern": {
                        "type": "string",
                        "description": "URL 匹配模式。根据 match_type 不同：contains 模式支持 * 通配符，exact 精确匹配，regex 正则匹配",
                    },
                    "response_body": {
                        "type": "string",
                        "description": "Mock 响应体（通常是 JSON 字符串）",
                    },
                    "method": {
                        "type": "string",
                        "description": "HTTP 方法过滤（GET/POST/PUT/DELETE），为空表示匹配所有方法",
                        "default": "",
                    },
                    "match_type": {
                        "type": "string",
                        "description": "匹配方式：contains（包含，默认，支持 * 通配符）、exact（精确匹配）、regex（正则匹配）",
                        "default": "contains",
                        "enum": ["contains", "exact", "regex"],
                    },
                    "status_code": {
                        "type": "integer",
                        "description": "响应状态码，默认 200",
                        "default": 200,
                    },
                    "response_headers": {
                        "type": "object",
                        "description": "响应头，默认 {'content-type': 'application/json'}",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "模拟延迟（毫秒），默认 0",
                        "default": 0,
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "是否启用，默认 true",
                        "default": True,
                    },
                },
                "required": ["name", "url_pattern", "response_body"],
            },
        ),
        Tool(
            name="mock_list",
            description="列出所有 mock 规则，显示规则名称、匹配模式、状态码、启用状态和命中次数。",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled_only": {
                        "type": "boolean",
                        "description": "是否只列出启用的规则，默认 false",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="mock_update",
            description="更新 mock 规则的指定字段。只需传入要修改的字段即可。",
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "规则 ID（从 mock_list 获取）",
                    },
                    "name": {
                        "type": "string",
                        "description": "新的规则名称",
                    },
                    "url_pattern": {
                        "type": "string",
                        "description": "新的 URL 匹配模式",
                    },
                    "response_body": {
                        "type": "string",
                        "description": "新的响应体",
                    },
                    "method": {
                        "type": "string",
                        "description": "新的 HTTP 方法过滤",
                    },
                    "match_type": {
                        "type": "string",
                        "description": "新的匹配方式",
                        "enum": ["contains", "exact", "regex"],
                    },
                    "status_code": {
                        "type": "integer",
                        "description": "新的状态码",
                    },
                    "response_headers": {
                        "type": "object",
                        "description": "新的响应头",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "新的延迟时间（毫秒）",
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="mock_delete",
            description="删除指定的 mock 规则",
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "规则 ID（从 mock_list 获取）",
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="mock_toggle",
            description="启用或禁用 mock 规则。可指定状态或自动切换。",
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "规则 ID",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "是否启用。不传则自动切换当前状态。",
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="mock_clear",
            description="清空所有 mock 规则",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mock_export",
            description="导出所有 mock 规则为 JSON 字符串，包含完整响应体。可用于备份或跨设备迁移。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mock_import",
            description="从 JSON 字符串导入 mock 规则。支持规则数组或 {\"rules\": [...]} 格式。",
            inputSchema={
                "type": "object",
                "properties": {
                    "rules_json": {
                        "type": "string",
                        "description": "规则 JSON 字符串（来自 mock_export 的 rules 字段）",
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "True 则追加到现有规则，False 则先清空再导入（默认）",
                        "default": False,
                    },
                },
                "required": ["rules_json"],
            },
        ),
        # Android 工具
        Tool(
            name="android_list_devices",
            description="列出所有连接的 Android 设备",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="android_get_device_info",
            description="获取指定 Android 设备的详细信息（型号、版本、是否 root 等）",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {
                        "type": "string",
                        "description": "设备序列号（从 android_list_devices 获取）",
                    },
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_setup_proxy",
            description="在 Android 设备上设置 HTTP 代理。注意：此方式对部分应用可能无效，建议在 Wi-Fi 设置中手动配置。",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {
                        "type": "string",
                        "description": "设备序列号",
                    },
                    "proxy_host": {
                        "type": "string",
                        "description": "代理服务器地址（通常是运行此服务的电脑 IP）",
                    },
                    "proxy_port": {
                        "type": "integer",
                        "description": "代理服务器端口，默认 8080",
                        "default": 8080,
                    },
                },
                "required": ["serial", "proxy_host"],
            },
        ),
        Tool(
            name="android_clear_proxy",
            description="清除 Android 设备上的代理设置",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {
                        "type": "string",
                        "description": "设备序列号",
                    },
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_get_proxy",
            description="读取 Android 设备当前的全局代理设置（用于诊断抓不到包的原因）",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_cert_status",
            description=(
                "检测 mitmproxy CA 证书在 Android 设备上的安装状态"
                "（用户库/系统库/APEX），并判断 App 是否会信任"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_push_cert",
            description=(
                "推送 mitmproxy CA 证书到 Android 设备的 /sdcard/Download 并返回安装指引"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_inject_system_cert",
            description=(
                "把 mitmproxy CA 注入 Android 系统凭据库（需要 root）。"
                "Android 13 及以下重挂载 /system 持久生效；"
                "Android 14+ 用 APEX tmpfs 覆盖，重启后失效"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
        # iOS 工具
        Tool(
            name="ios_list_devices",
            description="列出所有 iOS 设备（模拟器 + 真机）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ios_list_simulators",
            description="列出所有 iOS 模拟器",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ios_list_real_devices",
            description="列出所有 iOS 真机（需要安装 libimobiledevice）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ios_get_device_info",
            description="获取指定 iOS 设备的详细信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "udid": {
                        "type": "string",
                        "description": "设备 UDID（从 ios_list_devices 获取）",
                    },
                },
                "required": ["udid"],
            },
        ),
        Tool(
            name="ios_boot_simulator",
            description="启动 iOS 模拟器",
            inputSchema={
                "type": "object",
                "properties": {
                    "udid": {
                        "type": "string",
                        "description": "模拟器 UDID",
                    },
                },
                "required": ["udid"],
            },
        ),
        Tool(
            name="ios_shutdown_simulator",
            description="关闭 iOS 模拟器",
            inputSchema={
                "type": "object",
                "properties": {
                    "udid": {
                        "type": "string",
                        "description": "模拟器 UDID",
                    },
                },
                "required": ["udid"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """处理工具调用"""
    result: dict[str, Any]

    # 代理工具
    if name == "proxy_status":
        result = proxy_status()
    elif name == "proxy_start":
        result = proxy_start(
            port=arguments.get("port", 8888),
            setup_proxy=arguments.get("setup_proxy", False),
        )
    elif name == "proxy_stop":
        result = proxy_stop(port=arguments.get("port", 8888))
    elif name == "get_cert_info":
        result = get_cert_info()

    # 流量工具
    elif name == "traffic_list":
        result = traffic_list(
            limit=arguments.get("limit", 10),
            offset=arguments.get("offset", 0),
            filter_domain=arguments.get("filter_domain"),
            filter_type=arguments.get("filter_type"),
            filter_status=arguments.get("filter_status"),
            filter_url=arguments.get("filter_url"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
        )
    elif name == "traffic_get_detail":
        result = traffic_get_detail(arguments["request_id"])
    elif name == "traffic_search":
        result = traffic_search(
            keyword=arguments["keyword"],
            search_in=arguments.get("search_in"),
            method=arguments.get("method"),
            domain=arguments.get("domain"),
            context_chars=arguments.get("context_chars", 150),
            limit=arguments.get("limit", 10),
        )
    elif name == "traffic_read_body":
        result = traffic_read_body(
            request_id=arguments["request_id"],
            field=arguments.get("field", "response_body"),
            offset=arguments.get("offset", 0),
            length=arguments.get("length", 4000),
        )
    elif name == "traffic_clear":
        result = traffic_clear()

    # Mock 工具
    elif name == "mock_add":
        result = mock_add(
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
    elif name == "mock_list":
        result = mock_list(
            enabled_only=arguments.get("enabled_only", False),
        )
    elif name == "mock_update":
        result = mock_update(
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
    elif name == "mock_delete":
        result = mock_delete(arguments["rule_id"])
    elif name == "mock_toggle":
        result = mock_toggle(
            rule_id=arguments["rule_id"],
            enabled=arguments.get("enabled"),
        )
    elif name == "mock_clear":
        result = mock_clear()
    elif name == "mock_export":
        result = mock_export()
    elif name == "mock_import":
        result = mock_import(
            rules_json=arguments["rules_json"],
            merge=arguments.get("merge", False),
        )

    # Android 工具（异步）
    elif name == "android_list_devices":
        result = await android_list_devices()
    elif name == "android_get_device_info":
        result = await android_get_device_info(arguments["serial"])
    elif name == "android_setup_proxy":
        result = await android_setup_proxy(
            serial=arguments["serial"],
            proxy_host=arguments["proxy_host"],
            proxy_port=arguments.get("proxy_port", 8080),
        )
    elif name == "android_clear_proxy":
        result = await android_clear_proxy(arguments["serial"])
    elif name == "android_get_proxy":
        result = await android_get_proxy(arguments["serial"])
    elif name == "android_cert_status":
        result = await android_cert_status(arguments["serial"])
    elif name == "android_push_cert":
        result = await android_push_cert(arguments["serial"])
    elif name == "android_inject_system_cert":
        result = await android_inject_system_cert(arguments["serial"])

    # iOS 工具（异步）
    elif name == "ios_list_devices":
        result = await ios_list_devices()
    elif name == "ios_list_simulators":
        result = await ios_list_simulators()
    elif name == "ios_list_real_devices":
        result = await ios_list_real_devices()
    elif name == "ios_get_device_info":
        result = await ios_get_device_info(arguments["udid"])
    elif name == "ios_boot_simulator":
        result = await ios_boot_simulator(arguments["udid"])
    elif name == "ios_shutdown_simulator":
        result = await ios_shutdown_simulator(arguments["udid"])

    else:
        result = {"error": f"Unknown tool: {name}"}

    # 格式化输出
    import json
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def run_server():
    """运行 MCP 服务器"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    """入口函数"""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
