"""Loopback cookie 会话端点测试。"""

from fastapi.testclient import TestClient


class _ClientHostMiddleware:
    """测试用 ASGI 中间件：覆盖 request.client.host。"""

    def __init__(self, app, host: str):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (self.host, 0)}
        await self.app(scope, receive, send)


def test_ui_session_sets_httponly_cookie_on_loopback():
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    response = client.post("/v1/ui/session")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "mitm_ui=" in response.headers.get("set-cookie", "")
    assert "HttpOnly" in response.headers["set-cookie"]
    health = client.get("/v1/health")
    assert health.status_code == 200


def test_ui_session_forbidden_when_not_loopback():
    from mitm_proxy_mcp.control.app import create_app

    app = create_app(token="secret")
    client = TestClient(_ClientHostMiddleware(app, "8.8.8.8"))
    response = client.post("/v1/ui/session")
    assert response.status_code == 403
    assert response.json()["detail"] == "loopback only"


def test_ui_session_ignores_x_forwarded_for():
    """实现必须看 request.client.host，不能信 X-Forwarded-For。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    response = client.post(
        "/v1/ui/session",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    assert response.status_code == 200
    assert "mitm_ui=" in response.headers.get("set-cookie", "")


def test_health_accepts_cookie_after_session():
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    client.post("/v1/ui/session")
    response = client.get("/v1/health")
    assert response.status_code == 200


def test_health_without_auth_returns_401():
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    assert client.get("/v1/health").status_code == 401


def test_bearer_takes_precedence_over_cookie():
    """Bearer 与 cookie 同时存在时，Bearer 优先。"""
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    client.post("/v1/ui/session")
    response = client.get(
        "/v1/health",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_invalid_cookie_returns_401():
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    client.cookies.set("mitm_ui", "wrong")
    assert client.get("/v1/health").status_code == 401


def test_v1_health_not_replaced_by_spa(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>ui</html>")
    from mitm_proxy_mcp.control import webui_dir
    monkeypatch.setattr(webui_dir, "webui_path", lambda: tmp_path)
    from mitm_proxy_mcp.control.app import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app(token="secret"))
    client.post("/v1/ui/session")
    assert client.get("/v1/health").json()["ok"] is True
    assert "ui" in client.get("/").text


def test_missing_webui_is_not_500(monkeypatch):
    from mitm_proxy_mcp.control import webui_dir
    monkeypatch.setattr(webui_dir, "webui_path", lambda: None)
    from mitm_proxy_mcp.control.app import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app(token="secret"))
    response = client.get("/")
    assert response.status_code in (503, 200)
    assert response.status_code != 500
