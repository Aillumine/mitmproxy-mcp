"""mitmproxy addon 模块测试"""

import importlib
import sqlite3
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def addon(tmp_path, monkeypatch):
    """把 addon 的库路径指到临时目录，避免污染 /tmp 下的真实数据"""
    monkeypatch.setenv("MITMPROXY_DB_PATH", str(tmp_path / "traffic.db"))
    monkeypatch.setenv("MITMPROXY_MOCK_DB_PATH", str(tmp_path / "mock.db"))

    from mitm_proxy_mcp.addon import traffic_addon

    importlib.reload(traffic_addon)
    return traffic_addon


class TestInitDb:
    """建表"""

    def test_creates_traffic_table(self, addon, tmp_path):
        """init_db 建出 traffic 表"""
        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()

        assert ("traffic",) in rows


class TestInferResourceType:
    """资源类型推断"""

    def test_json_is_xhr(self, addon):
        assert addon.infer_resource_type("application/json", "https://a.com/x") == "XHR"

    def test_png_is_image(self, addon):
        assert addon.infer_resource_type("image/png", "https://a.com/x.png") == "Image"

    def test_html_is_document(self, addon):
        assert addon.infer_resource_type("text/html", "https://a.com/") == "Document"


class TestLooksLikeHtml:
    """Google Translate 这类接口常返回 HTML 片段，Content-Type 却不是 text/html。"""

    def test_html_fragment(self, addon):
        assert addon.looks_like_html(b"<p>hello</p>") is True
        assert addon.looks_like_html(b'{"ok":true}') is False

    def test_plain_html_body_is_stored_as_document(self, addon, tmp_path):
        addon.response(
            _http_flow(
                method="GET",
                url="https://translate.google.com/m?client=gtx&sl=auto&tl=pt",
                host="translate.google.com",
                content=b"<p><em>hello</em></p>",
                response_headers={"content-type": "text/plain; charset=utf-8"},
            )
        )
        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        row = conn.execute("SELECT resource_type FROM traffic").fetchone()
        conn.close()
        assert row[0] == "Document"


class TestMatchUrl:
    """Mock 规则 URL 匹配"""

    def test_contains(self, addon):
        assert addon.match_url("https://a.com/v1/user", "/v1/user", "contains") is True

    def test_contains_wildcard(self, addon):
        assert (
            addon.match_url(
                "https://a.com/v1/abc/user", "/v1/*/user", "contains"
            )
            is True
        )

    def test_exact(self, addon):
        assert addon.match_url("https://a.com/x", "https://a.com/x", "exact") is True
        assert addon.match_url("https://a.com/xy", "https://a.com/x", "exact") is False

    def test_regex(self, addon):
        assert addon.match_url("https://a.com/v1/pay/go", r"/v1/pay/.*", "regex") is True

    def test_invalid_regex_does_not_raise(self, addon):
        """规则里写了坏正则不能把整个代理搞崩"""
        assert addon.match_url("https://a.com/x", "[unclosed", "regex") is False


class TestTlsFailure:
    """TLS 握手失败记录"""

    def _fake_tls_data(self, sni, error):
        """构造最小的 TlsData 替身，只带 addon 实际读到的字段"""

        class Conn:
            pass

        class Data:
            pass

        conn = Conn()
        conn.sni = sni
        conn.error = error
        conn.peername = ("192.168.1.50", 51234)

        data = Data()
        data.conn = conn
        return data

    def test_records_failed_handshake(self, addon, tmp_path):
        """握手失败写入一条 status=0 的 TLS 记录"""
        addon.tls_failed_client(
            self._fake_tls_data("api.flowgpt.com", "certificate verify failed")
        )

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        rows = conn.execute(
            "SELECT id, method, domain, status, resource_type, error FROM traffic"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        record_id, method, domain, status, resource_type, error = rows[0]
        assert record_id.startswith("tls-")
        assert method == "CONNECT"
        assert domain == "api.flowgpt.com"
        assert status == 0
        assert resource_type == "TLS"
        assert "certificate verify failed" in error

    def test_missing_sni_does_not_crash(self, addon, tmp_path):
        """没有 SNI 的连接不能让 addon 抛异常，否则会拖垮代理"""
        addon.tls_failed_client(self._fake_tls_data(None, "unknown ca"))

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        count = conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]
        conn.close()

        assert count == 1

    def test_records_are_distinguishable(self, addon, tmp_path):
        """多次失败要产生多条记录，不能相互覆盖"""
        addon.tls_failed_client(self._fake_tls_data("a.com", "err1"))
        addon.tls_failed_client(self._fake_tls_data("b.com", "err2"))

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        count = conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]
        conn.close()

        assert count == 2


def _http_flow(
    *,
    method: str,
    url: str,
    host: str,
    status: int = 200,
    content: bytes = b"",
    request_headers: dict | None = None,
    response_headers: dict | None = None,
) -> MagicMock:
    flow = MagicMock()
    flow.request.method = method
    flow.request.pretty_url = url
    flow.request.host = host
    flow.request.headers = request_headers or {}
    flow.request.content = b""
    flow.request.timestamp_start = 1.0
    flow.response.status_code = status
    flow.response.headers = response_headers or {}
    flow.response.content = content
    flow.response.timestamp_end = 1.1
    flow.response.stream = False
    return flow


class TestSkipConnectTunnels:
    """HTTPS CONNECT 只是建隧道，不应出现在流量列表里"""

    def test_response_does_not_record_connect(self, addon, tmp_path):
        addon.response(
            _http_flow(
                method="CONNECT",
                url="https://api.example.com:443/",
                host="api.example.com",
            )
        )

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        count = conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]
        conn.close()
        assert count == 0

    def test_response_still_records_get(self, addon, tmp_path):
        addon.response(
            _http_flow(
                method="GET",
                url="https://api.example.com/v1/user",
                host="api.example.com",
                content=b'{"ok":true}',
            )
        )

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        rows = conn.execute("SELECT method, url FROM traffic").fetchall()
        conn.close()
        assert rows == [("GET", "https://api.example.com/v1/user")]

    def test_request_does_not_mock_connect(self, addon, tmp_path):
        mock_db = tmp_path / "mock.db"
        conn = sqlite3.connect(str(mock_db))
        conn.execute(
            """
            CREATE TABLE mock_rules (
                id TEXT PRIMARY KEY,
                name TEXT,
                url_pattern TEXT,
                match_type TEXT,
                method TEXT,
                status_code INTEGER,
                response_headers TEXT,
                response_body TEXT,
                delay_ms INTEGER,
                enabled INTEGER,
                updated_at REAL,
                hit_count INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mock_rules VALUES (
                'r1', 'all', '/', 'contains', '', 200, '{}', '{}', 0, 1, 1, 0
            )
            """
        )
        conn.commit()
        conn.close()

        flow = _http_flow(
            method="CONNECT",
            url="https://api.example.com:443/",
            host="api.example.com",
        )
        flow.response = None
        addon.request(flow)
        assert flow.response is None


class TestCaptureBodyPolicy:
    """大图/视频走流式，不落库；JSON / WebSocket 握手保持原样。"""

    def test_json_is_stored_not_streamed(self, addon):
        assert addon.should_store_body("application/json", "https://api.example.com/v1") is True
        assert addon.should_stream_body("application/json", "https://api.example.com/v1") is False

    def test_images_and_video_are_streamed(self, addon):
        assert addon.should_stream_body("image/png", "https://cdn.example.com/a.png") is True
        assert addon.should_store_body("image/png", "https://cdn.example.com/a.png") is False
        assert addon.should_stream_body("video/mp4", "https://cdn.example.com/a.mp4") is True

    def test_websocket_handshake_is_never_streamed(self, addon):
        headers = {"Upgrade": "websocket"}
        assert addon.should_stream_body("", "wss://api.example.com/ws", headers) is False
        assert addon.should_store_body("", "wss://api.example.com/ws", headers) is True

    def test_responseheaders_streams_images_only(self, addon):
        image = _http_flow(
            method="GET",
            url="https://cdn.example.com/a.png",
            host="cdn.example.com",
            response_headers={"content-type": "image/png", "content-length": "4096"},
        )
        image.response.stream = False
        addon.responseheaders(image)
        assert image.response.stream is True

        api = _http_flow(
            method="GET",
            url="https://api.example.com/v1/user",
            host="api.example.com",
            response_headers={"content-type": "application/json"},
        )
        api.response.stream = False
        addon.responseheaders(api)
        assert api.response.stream is False

        ws = _http_flow(
            method="GET",
            url="wss://api.example.com/ws",
            host="api.example.com",
            request_headers={"Upgrade": "websocket"},
        )
        ws.response.stream = False
        addon.responseheaders(ws)
        assert ws.response.stream is False

    def test_image_record_skips_blob_but_keeps_metadata(self, addon, tmp_path):
        class Guard:
            def __init__(self):
                self.headers = {"content-type": "image/png", "content-length": "2048"}
                self.status_code = 200
                self.timestamp_end = 1.1
                self.stream = True

            @property
            def content(self):
                raise AssertionError("streamed image must not buffer content")

        flow = _http_flow(
            method="GET",
            url="https://cdn.example.com/a.png",
            host="cdn.example.com",
        )
        flow.response = Guard()
        addon.response(flow)

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        row = conn.execute(
            "SELECT method, url, status, size, response_body FROM traffic"
        ).fetchone()
        conn.close()
        assert row[0] == "GET"
        assert row[1] == "https://cdn.example.com/a.png"
        assert row[2] == 200
        assert row[3] == 2048
        assert row[4] in (None, b"")

    def test_json_record_still_stores_body(self, addon, tmp_path):
        addon.response(
            _http_flow(
                method="POST",
                url="https://api.example.com/prompt/v2/filter",
                host="api.example.com",
                content=b'{"ok":true}',
                response_headers={"content-type": "application/json"},
            )
        )
        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        row = conn.execute("SELECT response_body FROM traffic").fetchone()
        conn.close()
        assert row[0] == b'{"ok":true}'


class TestWebsocketCapture:
    """握手改写成 wss://，后续帧单独入库，不能只留一条空的 101。"""

    def test_websocket_url_rewrites_https(self, addon):
        assert addon.websocket_url(
            "https://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket"
        ) == "wss://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket"

    def test_handshake_is_stored_as_wss(self, addon, tmp_path):
        addon.response(
            _http_flow(
                method="GET",
                url="https://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket",
                host="staging-ws-flow-dev.flowgpt.com",
                status=101,
                request_headers={"Upgrade": "websocket"},
                response_headers={"Upgrade": "websocket"},
            )
        )
        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        row = conn.execute("SELECT method, url, status, resource_type FROM traffic").fetchone()
        conn.close()
        assert row[0] == "GET"
        assert row[1].startswith("wss://staging-ws-flow-dev.flowgpt.com/")
        assert row[2] == 101
        assert row[3] == "WebSocket"

    def test_websocket_message_stores_client_and_server_frames(self, addon, tmp_path):
        flow = _http_flow(
            method="GET",
            url="https://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket",
            host="staging-ws-flow-dev.flowgpt.com",
            status=101,
            request_headers={"Upgrade": "websocket"},
        )

        class Msg:
            def __init__(self, from_client, content):
                self.from_client = from_client
                self.content = content

        class Ws:
            messages = []

        flow.websocket = Ws()
        flow.websocket.messages = [Msg(True, b"40")]
        addon.websocket_message(flow)
        flow.websocket.messages = [Msg(True, b"40"), Msg(False, b'0{"sid":"abc"}')]
        addon.websocket_message(flow)

        conn = sqlite3.connect(str(tmp_path / "traffic.db"))
        rows = conn.execute(
            "SELECT method, url, resource_type, request_body, response_body FROM traffic ORDER BY id"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][0] == "WS"
        assert rows[0][1].startswith("wss://")
        assert rows[0][2] == "WebSocket"
        assert rows[0][3] == b"40"
        assert rows[1][4] == b'0{"sid":"abc"}'


    def test_strips_permessage_deflate_so_socketio_open_can_pass(self, addon):
        flow = _http_flow(
            method="GET",
            url="https://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket",
            host="staging-ws-flow-dev.flowgpt.com",
            request_headers={
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Extensions": "permessage-deflate",
            },
        )
        flow.response = None
        addon.request(flow)
        assert flow.response is None
        keys = {str(key).lower() for key in flow.request.headers}
        assert "sec-websocket-extensions" not in keys
