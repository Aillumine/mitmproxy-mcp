"""FastAPI 控制服务接口测试。"""

import os
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from mitm_proxy_mcp.control.runtime import RuntimeInfo


def _runtime() -> RuntimeInfo:
    return RuntimeInfo(
        version=1,
        pid=os.getpid(),
        base_url="http://127.0.0.1:18765",
        token="secret",
        traffic_db="/tmp/traffic.db",
        mock_db="/tmp/mock.db",
        proxy_port=8888,
        capture_target="mac",
        started_at="2026-08-06T00:00:00Z",
    )


def test_health_requires_valid_bearer_token():
    """健康检查拒绝缺失或错误的 Bearer token。"""
    from mitm_proxy_mcp.control.app import create_app

    app = create_app(token="secret", proxy_status=lambda: {"running": True})
    client = TestClient(app)

    assert client.get("/v1/health").status_code == 401
    assert (
        client.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    response = client.get(
        "/v1/health", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "version": "1.0.0",
        "proxy_running": True,
        "pid": os.getpid(),
    }


def test_docs_endpoints_are_disabled():
    """控制服务不暴露未经认证的 API 文档。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_runtime_omits_bearer_token():
    """运行时端点绝不暴露控制服务 token。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(
        create_app(token="secret", read_runtime=lambda: _runtime())
    )

    response = client.get("/v1/runtime", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json()["base_url"] == "http://127.0.0.1:18765"
    assert "token" not in response.json()


def test_tools_lists_dispatcher_tools():
    """工具清单来自注入的当前分发表。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(
        create_app(token="secret", list_tools=lambda: ["proxy_status", "mock_list"])
    )

    response = client.get("/v1/tools", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json() == {"tools": ["proxy_status", "mock_list"]}


def test_tool_invocation_forwards_json_arguments():
    """工具端点把 JSON 请求体转发至分发器。"""
    from mitm_proxy_mcp.control.app import create_app

    invoke_tool = AsyncMock(return_value={"running": False, "message": "stopped"})
    client = TestClient(create_app(token="secret", invoke_tool=invoke_tool))

    response = client.post(
        "/v1/tools/proxy_status",
        headers={"Authorization": "Bearer secret"},
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {"running": False, "message": "stopped"}
    invoke_tool.assert_awaited_once_with("proxy_status", {})


def test_unknown_tool_returns_404():
    """未知工具映射为 404。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(
        create_app(token="secret", invoke_tool=AsyncMock(side_effect=KeyError("nope")))
    )

    response = client.post(
        "/v1/tools/nope", headers={"Authorization": "Bearer secret"}, json={}
    )

    assert response.status_code == 404


def test_tool_failure_returns_standard_error_body():
    """工具调用异常返回控制服务约定的错误体。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(
        create_app(
            token="secret",
            invoke_tool=AsyncMock(side_effect=RuntimeError("database unavailable")),
        )
    )

    response = client.post(
        "/v1/tools/traffic_list",
        headers={"Authorization": "Bearer secret"},
        json={},
    )

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "message": "database unavailable",
    }


def test_events_is_not_implemented():
    """事件流端点明确尚未实现。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))

    assert (
        client.get("/v1/events", headers={"Authorization": "Bearer secret"}).status_code
        == 501
    )
