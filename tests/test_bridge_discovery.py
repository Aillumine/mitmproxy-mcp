from __future__ import annotations

import json

import httpx
import pytest

from mitm_proxy_mcp.control.runtime import RuntimeInfo


def _runtime() -> RuntimeInfo:
    return RuntimeInfo(
        version=1,
        pid=123,
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
