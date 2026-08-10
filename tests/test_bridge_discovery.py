from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from mitm_proxy_mcp.control.runtime import RuntimeInfo


def _runtime(pid: int | None = None) -> RuntimeInfo:
    return RuntimeInfo(
        version=1,
        pid=os.getpid() if pid is None else pid,
        base_url="http://127.0.0.1:18765",
        token="test-token",
        traffic_db="/tmp/traffic.db",
        mock_db="/tmp/mock.db",
        proxy_port=8888,
        capture_target="default",
        started_at="2026-08-06T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_resolve_uses_healthy_runtime(monkeypatch):
    from mitm_proxy_mcp.bridge.discovery import resolve_backend

    info = _runtime()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery.read_runtime", lambda: info
    )
    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.httpx.AsyncClient", MockAsyncClient
    )

    client = await resolve_backend(launch=False)

    assert client is not None
    assert requests[0].url == httpx.URL(f"{info.base_url}/v1/health")
    assert requests[0].headers["authorization"] == f"Bearer {info.token}"


@pytest.mark.asyncio
async def test_call_tool_posts_arguments_and_returns_response(monkeypatch):
    from mitm_proxy_mcp.bridge.client import ControlClient

    request_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.httpx.AsyncClient", MockAsyncClient
    )

    result = await ControlClient.from_runtime(_runtime()).call_tool(
        "proxy_status", {"verbose": True}
    )

    assert result == {"success": True}
    assert request_body == {"verbose": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (
            500,
            {"success": False, "message": "database unavailable"},
            {"success": False, "message": "database unavailable"},
        ),
        (
            404,
            {"detail": "unknown tool: nope"},
            {"success": False, "message": "unknown tool: nope"},
        ),
    ],
)
async def test_tool_failure_is_returned_not_raised(
    monkeypatch, status_code, body, expected
):
    """控制服务里的工具失败要原样返回，不能被当成链路故障。"""
    from mitm_proxy_mcp.bridge.client import ControlClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.httpx.AsyncClient", MockAsyncClient
    )

    result = await ControlClient.from_runtime(_runtime()).call_tool("traffic_list", {})

    assert result == expected


@pytest.mark.asyncio
async def test_unparseable_error_response_raises(monkeypatch):
    """无法解析的错误响应仍然抛出，交给上层回退。"""
    from mitm_proxy_mcp.bridge.client import ControlClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.httpx.AsyncClient", MockAsyncClient
    )

    with pytest.raises(httpx.HTTPStatusError):
        await ControlClient.from_runtime(_runtime()).call_tool("traffic_list", {})


@pytest.mark.asyncio
async def test_call_tool_read_timeout_covers_slow_tools(monkeypatch):
    """工具调用的读超时必须容纳 proxy_start / ios_boot 这类慢操作。"""
    from mitm_proxy_mcp.bridge.client import ControlClient

    timeouts: list[httpx.Timeout] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            timeouts.append(kwargs["timeout"])
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.httpx.AsyncClient", MockAsyncClient
    )

    await ControlClient.from_runtime(_runtime()).call_tool("proxy_start", {})

    timeout = timeouts[0]
    assert timeout.connect == 5.0
    assert timeout.read is not None and timeout.read >= 120.0


@pytest.mark.asyncio
async def test_call_tool_waits_for_slow_handler(monkeypatch):
    """真实连接下，处理耗时远超连接超时的工具仍能拿到结果。

    走真实 socket 才能验证读超时；MockTransport 不会执行任何超时。
    """
    from mitm_proxy_mcp.bridge import client as client_module
    from mitm_proxy_mcp.bridge.client import ControlClient

    body = b'{"success": true, "slow": true}'

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(0.4)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    # 连接超时压到 0.05s：若读阶段错误地复用了它，0.4s 的响应必然超时。
    monkeypatch.setattr(client_module, "CONNECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(client_module, "READ_TIMEOUT_S", 10.0)

    client = ControlClient(base_url=f"http://127.0.0.1:{port}", token="test-token")
    try:
        result = await client.call_tool("proxy_start", {})
    finally:
        server.close()
        await server.wait_closed()

    assert result == {"success": True, "slow": True}


@pytest.mark.asyncio
async def test_resolve_launches_then_polls_until_runtime_is_healthy(
    monkeypatch, tmp_path
):
    from mitm_proxy_mcp.bridge.discovery import resolve_backend

    info = _runtime()
    reads = iter([None, info])
    launched: list[object] = []

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery.read_runtime", lambda: next(reads)
    )
    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery._launch_control",
        lambda: launched.append(object()) or object(),
    )

    async def healthy(self):
        return True

    monkeypatch.setattr("mitm_proxy_mcp.bridge.client.ControlClient.health", healthy)

    client = await resolve_backend(timeout_s=0.2)

    assert client is not None
    assert len(launched) == 1


@pytest.mark.asyncio
async def test_resolve_ignores_runtime_of_dead_process(monkeypatch):
    """runtime.json 指向已退出的进程时不当作可用后端。"""
    from mitm_proxy_mcp.bridge.discovery import resolve_backend

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery.read_runtime",
        lambda: _runtime(pid=999_999_999),
    )

    async def unexpected_health(self):
        raise AssertionError("不应对已退出的进程发起健康检查")

    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.client.ControlClient.health", unexpected_health
    )

    assert await resolve_backend(launch=False) is None


@pytest.mark.asyncio
async def test_resolve_logs_warning_when_launch_fails(monkeypatch):
    """无法拉起控制服务时留下告警日志，而不是静默降级。"""
    from loguru import logger

    from mitm_proxy_mcp.bridge.discovery import resolve_backend

    monkeypatch.setattr("mitm_proxy_mcp.bridge.discovery.read_runtime", lambda: None)
    monkeypatch.setattr("mitm_proxy_mcp.bridge.discovery._launch_control", lambda: None)

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message), level="WARNING")
    try:
        assert await resolve_backend() is None
    finally:
        logger.remove(sink_id)

    assert any("控制服务" in message for message in messages)


@pytest.mark.asyncio
async def test_resolve_returns_none_when_unhealthy(monkeypatch):
    from mitm_proxy_mcp.bridge.discovery import resolve_backend

    monkeypatch.setattr("mitm_proxy_mcp.bridge.discovery.read_runtime", lambda: None)
    monkeypatch.setattr("mitm_proxy_mcp.bridge.discovery._launch_control", lambda: None)

    assert await resolve_backend(launch=False) is None


def test_launch_control_uses_uv_project_root_and_appends_to_control_log(
    monkeypatch, tmp_path
):
    from mitm_proxy_mcp.bridge.discovery import _launch_control

    launches: list[tuple[list[str], dict[str, object]]] = []
    log_path = tmp_path / "control.log"

    monkeypatch.setattr("mitm_proxy_mcp.bridge.discovery.shutil.which", lambda _: "/uv")
    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery._get_project_root", lambda: tmp_path
    )
    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery.control_log_path", lambda: log_path
    )
    monkeypatch.setattr(
        "mitm_proxy_mcp.bridge.discovery.subprocess.Popen",
        lambda command, **kwargs: launches.append((command, kwargs)),
    )

    _launch_control()

    assert launches[0][0] == ["/uv", "run", "mitmproxy-control"]
    assert launches[0][1]["cwd"] == tmp_path
    assert launches[0][1]["start_new_session"] is True
    assert log_path.exists()
