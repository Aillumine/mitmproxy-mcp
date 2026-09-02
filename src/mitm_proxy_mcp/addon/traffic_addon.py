import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http

from mitm_proxy_mcp.core.throttle import ThrottleConfig, read_config, sleep_for_bytes, sleep_latency

# Paths come from the environment so this module stays importable — and
# therefore testable — instead of being generated as an f-string at runtime.
#
# 路径改从环境变量读取，这样本模块可以被 import（因而可测），
# 而不是运行时用 f-string 拼出来。
DB_PATH = os.environ.get("MITMPROXY_DB_PATH", "/tmp/mitmproxy-traffic.db")
MOCK_DB_PATH = os.environ.get("MITMPROXY_MOCK_DB_PATH", "/tmp/mitmproxy-mock.db")
THROTTLE_PATH = os.environ.get("MITMPROXY_THROTTLE_PATH", "/tmp/mitmproxy-throttle.json")
CAPTURE_BODY_LIMIT = 1_048_576
_BINARY_MIME_PREFIXES = ("image/", "video/", "audio/", "font/")
_BINARY_MIMES = {"application/octet-stream", "application/wasm"}

_traffic_conn: sqlite3.Connection | None = None
_mock_cache: dict = {"mtime": None, "rules": None}
_throttle_cache: dict = {"mtime": None, "config": ThrottleConfig()}


def _get_traffic_conn() -> sqlite3.Connection:
    global _traffic_conn
    if _traffic_conn is None:
        _traffic_conn = sqlite3.connect(DB_PATH, timeout=10)
        _traffic_conn.execute("PRAGMA journal_mode=WAL")
        _traffic_conn.execute("PRAGMA synchronous=NORMAL")
    return _traffic_conn


def init_db():
    conn = _get_traffic_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS traffic (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            domain TEXT NOT NULL,
            status INTEGER NOT NULL,
            resource_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            time_ms REAL NOT NULL,
            request_headers TEXT,
            request_body BLOB,
            request_body_size INTEGER DEFAULT 0,
            response_headers TEXT,
            response_body BLOB,
            timing TEXT,
            error TEXT
        )
    """)
    conn.commit()


def _header_map(headers) -> dict[str, str]:
    if not headers:
        return {}
    try:
        return {str(key): str(value) for key, value in dict(headers).items()}
    except Exception:
        return {}


def _header_ci(headers: dict[str, str], name: str) -> str:
    want = name.lower()
    for key, value in headers.items():
        if key.lower() == want:
            return value
    return ""


def _clean_mime(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def is_websocket_handshake(headers) -> bool:
    mapping = headers if isinstance(headers, dict) else _header_map(headers)
    return _header_ci(mapping, "upgrade").lower() == "websocket"


def declared_body_size(headers) -> int:
    mapping = headers if isinstance(headers, dict) else _header_map(headers)
    raw = _header_ci(mapping, "content-length")
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def should_store_body(content_type: str, url: str, headers=None) -> bool:
    """JSON/文本/接口 body 入库；图片视频等二进制不入库。WebSocket 握手始终保留。"""
    mapping = headers if isinstance(headers, dict) else _header_map(headers or {})
    if is_websocket_handshake(mapping):
        return True
    mime = _clean_mime(content_type)
    if mime.startswith(_BINARY_MIME_PREFIXES) or mime in _BINARY_MIMES:
        return False
    resource = infer_resource_type(content_type, url)
    return resource not in ("Image", "Media", "Font")


def should_stream_body(content_type: str, url: str, headers=None) -> bool:
    """流式=边收边转发给客户端，不在代理里拼完整 body。WSS 握手绝不能 stream。"""
    mapping = headers if isinstance(headers, dict) else _header_map(headers or {})
    if is_websocket_handshake(mapping):
        return False
    return not should_store_body(content_type, url, mapping)


def websocket_url(url: str) -> str:
    """mitmproxy 把 WSS 记成 https://，列表分组需要还原成 wss://。"""
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url

def infer_resource_type(mime_type: str, url: str) -> str:
    """推断资源类型"""
    if not mime_type:
        mime_type = ""
    mime_lower = mime_type.split(";")[0].strip().lower()
    
    try:
        parsed = urlparse(url)
        ext = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
    except:
        ext = ""
    
    if "text/html" in mime_lower or ext in (".html", ".htm"):
        return "Document"
    if "text/css" in mime_lower or ext == ".css":
        return "Stylesheet"
    if mime_lower.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico"):
        return "Image"
    if "javascript" in mime_lower or ext in (".js", ".mjs"):
        return "Script"
    if mime_lower.startswith("video/") or mime_lower.startswith("audio/") or ext in (
        ".mp4", ".mp3", ".webm", ".ogg", ".wav", ".m4a", ".mov",
    ):
        return "Media"
    if "font" in mime_lower or ext in (".woff", ".woff2", ".ttf", ".otf"):
        return "Font"
    if "json" in mime_lower or "xml" in mime_lower or ext in (".json", ".xml"):
        return "XHR"
    if mime_lower.startswith("application/"):
        return "XHR"
    
    return "Other"


_HTML_START = re.compile(
    r"^(?:<!doctype\s+html|<!--|<html[\s>]|<[a-z][\w:.-]*(?:[\s/>]|$))",
    re.I,
)


def looks_like_html(raw) -> bool:
    """Content-Type 不是 text/html 时，仍把 HTML 片段归为 Document。"""
    if not raw:
        return False
    if isinstance(raw, bytes):
        text = raw[:512].decode("utf-8", errors="ignore")
    else:
        text = str(raw)[:512]
    text = text.lstrip("\ufeff \t\r\n")
    return bool(_HTML_START.match(text))

def match_url(url, pattern, match_type):
    """检查 URL 是否匹配 mock 规则"""
    if match_type == "exact":
        return url == pattern
    elif match_type == "regex":
        try:
            return bool(re.search(pattern, url))
        except re.error:
            return False
    else:  # contains
        if "*" in pattern:
            regex_pattern = re.escape(pattern).replace(r"\*", ".*")
            try:
                return bool(re.search(regex_pattern, url))
            except re.error:
                return False
        return pattern in url

def find_matching_mock(url, method):
    """查找匹配的 mock 规则（按 mock 库 mtime 缓存，避免每个请求扫盘）。"""
    if not Path(MOCK_DB_PATH).exists():
        _mock_cache["mtime"] = None
        _mock_cache["rules"] = []
        return None
    try:
        mtime = Path(MOCK_DB_PATH).stat().st_mtime
        rules = _mock_cache.get("rules")
        if rules is None or _mock_cache.get("mtime") != mtime:
            conn = sqlite3.connect(MOCK_DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM mock_rules WHERE enabled = 1 ORDER BY updated_at DESC"
            ).fetchall()
            conn.close()
            rules = [dict(row) for row in rows]
            _mock_cache["mtime"] = mtime
            _mock_cache["rules"] = rules

        method_upper = (method or "").upper()
        for rule in rules:
            rule_method = rule.get("method") or ""
            if rule_method and rule_method != method_upper:
                continue
            if match_url(url, rule["url_pattern"], rule.get("match_type") or "contains"):
                return rule
        return None
    except Exception as e:
        print(f"[Mock] Error reading rules: {e}")
        return None

def increment_mock_hit(rule_id):
    """递增 mock 命中计数"""
    try:
        conn = sqlite3.connect(MOCK_DB_PATH, timeout=5)
        conn.execute(
            "UPDATE mock_rules SET hit_count = hit_count + 1 WHERE id = ?",
            (rule_id,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_throttle_config() -> ThrottleConfig:
    """按 throttle.json mtime 缓存弱网配置，切换档位后无需重启代理。"""
    path = Path(THROTTLE_PATH)
    if not path.exists():
        _throttle_cache["mtime"] = None
        _throttle_cache["config"] = ThrottleConfig()
        return _throttle_cache["config"]
    try:
        mtime = path.stat().st_mtime
        cached = _throttle_cache.get("config")
        if cached is None or _throttle_cache.get("mtime") != mtime:
            config = read_config(path)
            _throttle_cache["mtime"] = mtime
            _throttle_cache["config"] = config
            return config
        return cached
    except Exception:
        return ThrottleConfig()


def _throttle_upload(raw) -> None:
    config = get_throttle_config()
    if not config.enabled:
        return
    sleep_latency(config)
    if raw:
        sleep_for_bytes(len(raw), config.upload_bps)


def _throttle_download_bytes(nbytes: int) -> None:
    config = get_throttle_config()
    if not config.enabled or nbytes <= 0:
        return
    sleep_for_bytes(nbytes, config.download_bps)


def _throttled_stream(download_bps: float):
    def stream(chunks):
        for chunk in chunks:
            data = chunk or b""
            if data and download_bps > 0:
                sleep_for_bytes(len(data), download_bps)
            yield chunk

    return stream


counter = [0]


def _is_connect_tunnel(flow) -> bool:
    return (getattr(flow.request, "method", "") or "").upper() == "CONNECT"


def _flow_headers(headers) -> dict[str, str]:
    if not headers:
        return {}
    if isinstance(headers, dict):
        return {str(key): str(value) for key, value in headers.items()}
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except Exception:
        return _header_map(headers)


def _store_payload(headers: dict[str, str], raw, store: bool) -> tuple[bytes | None, int]:
    if not store:
        return None, declared_body_size(headers)
    if not raw:
        return None, 0
    size = len(raw)
    if size > CAPTURE_BODY_LIMIT:
        return raw[:CAPTURE_BODY_LIMIT], size
    return raw, size


def _delete_header_ci(headers, name: str) -> None:
    if headers is None:
        return
    want = name.lower()
    try:
        for key in list(headers.keys()):
            if str(key).lower() == want:
                del headers[key]
    except Exception:
        if isinstance(headers, dict):
            for key in list(headers.keys()):
                if str(key).lower() == want:
                    headers.pop(key, None)


def _strip_websocket_extensions(flow) -> None:
    """
    去掉 permessage-deflate。

    OkHttp + Socket.IO 会协商压缩；mitmproxy 拆开再转发时，1 字节的
    Engine.IO ping/pong（2/3）能过，带 JSON 的 OPEN（0{sid}）会被弄坏，
    App 就表现为 WSS「连不上」，关掉代理则正常。
    """
    headers = getattr(getattr(flow, "request", None), "headers", None)
    if headers is None or not is_websocket_handshake(_flow_headers(headers)):
        return
    _delete_header_ci(headers, "sec-websocket-extensions")


def request(flow):
    """在请求阶段检查 mock 规则，匹配则直接返回 mock 响应"""
    if _is_connect_tunnel(flow):
        return
    _strip_websocket_extensions(flow)
    # 弱网上行：先按带宽「传完」请求体，再叠 RTT 延迟。
    try:
        req_raw = flow.request.raw_content or flow.request.content
    except Exception:
        req_raw = None
    _throttle_upload(req_raw)

    url = flow.request.pretty_url
    method = flow.request.method
    
    mock_rule = find_matching_mock(url, method)
    if mock_rule is None:
        return
    
    delay_ms = mock_rule.get("delay_ms", 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    headers = json.loads(mock_rule.get("response_headers", "{}"))
    headers["X-Mock-Rule"] = mock_rule["id"]
    
    body = (mock_rule.get("response_body", "") or "").encode("utf-8")
    _throttle_download_bytes(len(body))
    
    flow.response = http.Response.make(
        mock_rule.get("status_code", 200),
        body,
        headers,
    )
    
    increment_mock_hit(mock_rule["id"])
    
    counter[0] += 1
    print(f"[{counter[0]}] [MOCK] {method} {url[:80]} -> {mock_rule.get('status_code', 200)} (rule: {mock_rule['name']})")


def responseheaders(flow):
    """在 body 到达前决定是否流式转发。JSON/WSS 不 stream，避免抓不到内容。"""
    if _is_connect_tunnel(flow) or not getattr(flow, "response", None):
        return
    req_headers = _flow_headers(flow.request.headers)
    res_headers = _flow_headers(flow.response.headers)
    if is_websocket_handshake(req_headers):
        _delete_header_ci(flow.response.headers, "sec-websocket-extensions")
        return
    content_type = _header_ci(res_headers, "content-type")
    config = get_throttle_config()
    if should_stream_body(content_type, flow.request.pretty_url, req_headers):
        if config.enabled and config.download_bps > 0:
            flow.response.stream = _throttled_stream(config.download_bps)
            flow.metadata["throttle_streamed"] = True
        else:
            flow.response.stream = True


def response(flow):
    if _is_connect_tunnel(flow):
        return
    counter[0] += 1
    record_id = f"req-{counter[0]}"

    if not flow.response:
        return

    try:
        req_headers = _flow_headers(flow.request.headers)
        res_headers = _flow_headers(flow.response.headers)
        url = flow.request.pretty_url
        if is_websocket_handshake(req_headers):
            url = websocket_url(url)
        domain = flow.request.host
        content_type = _header_ci(res_headers, "content-type")
        resource_type = (
            "WebSocket" if is_websocket_handshake(req_headers)
            else infer_resource_type(content_type, url)
        )

        if flow.response.timestamp_end and flow.request.timestamp_start:
            time_ms = (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
        else:
            time_ms = 0.0

        store_res = should_store_body(content_type, url, req_headers)
        store_req = should_store_body(
            _header_ci(req_headers, "content-type"), url, req_headers
        )
        res_raw = flow.response.content if store_res else None
        req_raw = flow.request.content if store_req else None
        # 非流式响应在转发给客户端前按下行带宽限速（流式已在 stream 里限过）。
        if not flow.metadata.get("throttle_streamed"):
            try:
                raw = flow.response.raw_content or flow.response.content or b""
            except Exception:
                raw = res_raw or b""
            _throttle_download_bytes(len(raw) if raw else 0)
        if resource_type in ("Other", "XHR") and looks_like_html(res_raw):
            resource_type = "Document"
        res_body, size = _store_payload(res_headers, res_raw, store_res)
        req_body, req_size = _store_payload(req_headers, req_raw, store_req)

        conn = _get_traffic_conn()
        conn.execute("""
            INSERT OR REPLACE INTO traffic (
                id, timestamp, method, url, domain, status,
                resource_type, size, time_ms, request_headers,
                request_body, request_body_size, response_headers, response_body, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_id,
            time.time(),
            flow.request.method,
            url,
            domain,
            flow.response.status_code,
            resource_type,
            size,
            time_ms,
            json.dumps(req_headers),
            req_body,
            req_size,
            json.dumps(res_headers),
            res_body,
            None
        ))
        conn.commit()
        is_mock = _header_ci(res_headers, "x-mock-rule")
        tag = " [MOCK]" if is_mock else ""
        print(f"[{counter[0]}]{tag} {flow.request.method} {url[:80]}")
    except Exception as e:
        print(f"Error saving traffic: {e}")


def websocket_message(flow):
    """把 Socket.IO / WSS 帧写入流量表，而不是只留一条空的 HTTP 101。"""
    ws = getattr(flow, "websocket", None)
    messages = getattr(ws, "messages", None) if ws is not None else None
    if not messages:
        return
    msg = messages[-1]
    content = getattr(msg, "content", b"") or b""
    if len(content) > CAPTURE_BODY_LIMIT:
        content = content[:CAPTURE_BODY_LIMIT]
    from_client = bool(getattr(msg, "from_client", False))
    config = get_throttle_config()
    if config.enabled:
        bps = config.upload_bps if from_client else config.download_bps
        sleep_for_bytes(len(content), bps)
    req_headers = _flow_headers(getattr(flow.request, "headers", {}) or {})
    url = websocket_url(getattr(flow.request, "pretty_url", "") or "")
    domain = getattr(flow.request, "host", "") or ""
    counter[0] += 1
    try:
        conn = _get_traffic_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO traffic (
                id, timestamp, method, url, domain, status,
                resource_type, size, time_ms, request_headers,
                request_body, request_body_size, response_headers, response_body, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ws-{counter[0]}",
                time.time(),
                "WS",
                url,
                domain,
                101,
                "WebSocket",
                len(content),
                0.0,
                json.dumps(req_headers),
                content if from_client else None,
                len(content) if from_client else 0,
                "{}",
                None if from_client else content,
                None,
            ),
        )
        conn.commit()
        direction = "→" if from_client else "←"
        print(f"[{counter[0]}] [WS{direction}] {url[:80]}")
    except Exception as e:
        print(f"Error saving websocket: {e}")


def tls_failed_client(data):
    """
    记录与客户端的 TLS 握手失败

    证书绑定（SSL Pinning）的表现就是握手阶段直接失败，此时不存在
    http flow，原来的 response 钩子永远不会被调用，于是界面上什么都
    看不到。这里把失败连接也写进 traffic 表，让「抓不到包」变成一条
    可见的红色记录。
    """
    try:
        conn_obj = getattr(data, "conn", None)

        # Fall back defensively: a failed handshake may not have got far enough
        # to carry an SNI, and an exception here would take down the proxy.
        #
        # 防御式取值：握手失败时可能还没走到能拿到 SNI 的阶段，
        # 而这里抛异常会把整个代理拖垮。
        sni = getattr(conn_obj, "sni", None) or "unknown"
        if isinstance(sni, bytes):
            sni = sni.decode("utf-8", "ignore")

        error = getattr(conn_obj, "error", None) or "TLS handshake failed"

        counter[0] += 1
        record_id = f"tls-{counter[0]}"

        conn = _get_traffic_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO traffic (
                id, timestamp, method, url, domain, status, resource_type,
                size, time_ms, request_headers, request_body,
                request_body_size, response_headers, response_body, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                time.time(),
                "CONNECT",
                f"https://{sni}",
                sni,
                0,
                "TLS",
                0,
                0.0,
                "{}",
                None,
                0,
                "{}",
                None,
                str(error),
            ),
        )
        conn.commit()

        print(f"[{counter[0]}] [TLS-FAIL] {sni} -> {error}")

    except Exception as e:
        print(f"Error recording TLS failure: {e}")


init_db()
