import asyncio
import json
import os
import re
import sqlite3
import time
import zlib
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http
from mitmproxy.net import encoding as net_encoding

from mitm_proxy_mcp.core.throttle import (
    ThrottleConfig,
    latency_seconds,
    read_config,
    seconds_for_bytes,
)

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

# The addon writes straight to sqlite instead of going through SQLiteTrafficStore,
# so it never inherited that store's row cap and the file grew without bound.
#
# addon 直接写 sqlite，没走 SQLiteTrafficStore，因此一直没有它的行数上限，
# 库文件会无限增长。
MAX_TRAFFIC_ROWS = max(int(os.environ.get("MITMPROXY_MAX_ROWS") or 2000), 100)
PRUNE_EVERY = 200

# Only clients that are not this Mac get throttled. A phone reaches the proxy over
# the LAN address, while the Mac's own system proxy points at loopback.
#
# 只对「不是本机」的客户端限速：手机通过局域网地址连代理，
# 而 Mac 自己的系统代理指向回环地址。
_LOOPBACK_PREFIXES = ("127.", "::ffff:127.")
_LOOPBACK_HOSTS = {"::1", "localhost", "0.0.0.0", "::"}

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


def client_ip(flow) -> str:
    """Peer address of the client that opened this flow, "" when unavailable.

    发起这条流的客户端地址；取不到时返回空串。
    """
    conn = getattr(flow, "client_conn", None)
    peername = getattr(conn, "peername", None)
    if not peername:
        return ""
    try:
        host = peername[0]
    except (TypeError, IndexError):
        return ""
    if isinstance(host, bytes):
        host = host.decode("utf-8", "ignore")
    return str(host or "")


def is_device_client(flow) -> bool:
    """True only for an external device (phone), never for this Mac.

    Throttling must never touch the Mac's own browsing: its system proxy points at
    loopback, so anything from 127.x / ::1 is treated as local and left alone.
    When the peer address cannot be read we also treat it as local — better to
    under-throttle a phone than to slow down the machine running the proxy.

    仅当客户端是外部设备（手机）时为 True，本机永远不是。
    限速绝不能影响 Mac 自己上网：Mac 的系统代理指向回环地址，因此来自
    127.x / ::1 的连接一律视为本机、不限速。取不到对端地址时同样按本机处理——
    宁可漏限手机，也不能拖慢跑代理的这台机器。
    """
    ip = client_ip(flow)
    if not ip:
        return False
    if ip in _LOOPBACK_HOSTS or ip.startswith(_LOOPBACK_PREFIXES):
        return False
    return True


def throttle_applies(flow) -> ThrottleConfig | None:
    """当前档位对这条流生效时返回配置，否则 None。"""
    config = get_throttle_config()
    if not config.enabled or not is_device_client(flow):
        return None
    return config


async def _sleep(seconds: float) -> None:
    """Yield to the event loop instead of blocking it.

    mitmproxy runs every flow on one asyncio loop, so a blocking time.sleep()
    here would freeze all other connections — including the Mac's own traffic
    that is explicitly not supposed to be throttled.

    让出事件循环而不是阻塞它。
    mitmproxy 所有流跑在同一个 asyncio 循环上，这里用阻塞的 time.sleep() 会冻住
    其他所有连接——包括明确不该被限速的 Mac 自身流量。
    """
    if seconds > 0:
        await asyncio.sleep(seconds)


async def _throttle_upload(flow) -> None:
    """弱网上行：先按带宽「传完」请求体，再叠 RTT 延迟。"""
    config = throttle_applies(flow)
    if config is None:
        return
    await _sleep(latency_seconds(config))
    # Only decode the request body once throttling is actually in play — doing it
    # unconditionally cost a full body decode on every single request.
    #
    # 只有真的要限速时才解码请求体——之前无条件解码，等于每个请求白跑一次解码。
    try:
        raw = flow.request.raw_content or flow.request.content
    except Exception:
        raw = None
    if raw:
        await _sleep(seconds_for_bytes(len(raw), config.upload_bps))


def _forwarded_size(flow) -> int:
    """转发给客户端的实际字节数（压缩后），限速按它计时。"""
    try:
        raw = flow.response.raw_content
    except Exception:
        raw = None
    if raw:
        return len(raw)
    return declared_body_size(_flow_headers(getattr(flow.response, "headers", {})))


async def _throttle_download(flow, nbytes: int | None = None) -> None:
    """弱网下行：body 已在代理内收齐，按带宽等待后再转发给客户端。"""
    config = throttle_applies(flow)
    if config is None:
        return
    # 体积在这里才算——限速关闭时不该为此多做任何事。
    if nbytes is None:
        nbytes = _forwarded_size(flow)
    if nbytes <= 0:
        return
    await _sleep(seconds_for_bytes(nbytes, config.download_bps))


def _make_tee_stream(flow) -> Callable[[bytes], bytes]:
    """Forward each chunk immediately while keeping a copy for the traffic table.

    Buffering a response end-to-end (the old behaviour for every JSON/HTML/SSE
    body) held the whole payload until the server finished — SSE and chunked
    APIs arrived in one burst at the end. Teeing keeps capture intact without
    ever delaying the client.

    边转发边留一份副本给流量表。
    旧行为是把 JSON/HTML/SSE 的 body 整包缓冲，要等服务端发完才转给客户端——
    SSE 和 chunked 接口会在最后一次性涌出来。改成 tee 后既不耽误客户端，
    也照样能抓到 body。
    """
    capture: dict = {"buf": bytearray(), "size": 0, "truncated": False, "used": False}
    meta = getattr(flow, "metadata", None)
    if isinstance(meta, dict):
        meta["capture_body"] = capture

    def tee(chunk: bytes) -> bytes:
        # mitmproxy always ends a streamed body with an empty chunk, so this flag
        # is set for a genuinely empty body but stays False if the tee never ran.
        #
        # mitmproxy 结束流式 body 时一定会再调一次空 chunk，因此真的空 body 也会
        # 置位；tee 压根没被调用时才保持 False。
        capture["used"] = True
        if chunk:
            capture["size"] += len(chunk)
            room = CAPTURE_BODY_LIMIT - len(capture["buf"])
            if room > 0:
                capture["buf"] += chunk[:room]
            # 单个 chunk 就超限时也要置位，不能只看「已经装满」的情况。
            if capture["size"] > CAPTURE_BODY_LIMIT:
                capture["truncated"] = True
        return chunk

    return tee


def _decode_captured(raw: bytes, content_encoding: str) -> bytes:
    """Undo Content-Encoding on teed bytes, which arrive still compressed.

    A truncated body cannot be decompressed in one shot, so gzip/deflate fall
    back to an incremental decompressor that returns whatever it managed to
    read; anything still undecodable is stored as-is rather than dropped.

    对 tee 下来的字节做 Content-Encoding 解码——流式拿到的是压缩后的原始字节。
    截断的 body 无法整体解压，gzip/deflate 退回增量解压器，能解多少算多少；
    仍解不开的就原样入库，而不是丢弃。
    """
    enc = (content_encoding or "").strip().lower()
    if not raw or not enc or enc == "identity":
        return raw
    try:
        decoded = net_encoding.decode(raw, enc)
        if isinstance(decoded, str):
            return decoded.encode("utf-8", "replace")
        if decoded is not None:
            return decoded
    except Exception:
        pass
    if enc in ("gzip", "x-gzip", "deflate"):
        wbits = zlib.MAX_WBITS | 16 if enc != "deflate" else zlib.MAX_WBITS
        try:
            return zlib.decompressobj(wbits).decompress(raw)
        except Exception:
            pass
    return raw


def _capture_slot(flow) -> dict | None:
    """这条流的 tee 缓冲；没走 tee（或 metadata 不可用）时为 None。"""
    meta = getattr(flow, "metadata", None)
    if not isinstance(meta, dict):
        return None
    capture = meta.get("capture_body")
    return capture if isinstance(capture, dict) else None


def _response_body_for_store(flow, res_headers: dict[str, str], store: bool):
    """返回 (已解码的 body, 线上实际字节数)；未 tee 的流走原来的 content 路径。"""
    capture = _capture_slot(flow)
    # A response set by an addon (a mock) never goes through the stream callback:
    # mitmproxy only emulates the responseheaders hook and then sends it directly.
    # Trusting the untouched buffer there would store an empty mock body.
    #
    # addon 自己生成的响应（mock）不会走 stream 回调：mitmproxy 只是「模拟」触发
    # responseheaders，随后直接发出去。此时若信任没被写过的缓冲，
    # 会把 mock 的 body 存成空。
    if capture is not None and capture.get("used"):
        raw = _decode_captured(
            bytes(capture["buf"]), _header_ci(res_headers, "content-encoding")
        )
        return (raw if store else None), int(capture["size"])
    if not store:
        return None, 0
    try:
        return flow.response.content, 0
    except Exception:
        return None, 0


counter = [0]
_since_prune = [0]


def _prune_traffic(conn: sqlite3.Connection) -> None:
    """Drop the oldest rows once the table passes MAX_TRAFFIC_ROWS.

    Checked every PRUNE_EVERY inserts so the COUNT(*) cost is amortised instead
    of being paid on the proxy's event loop for every single flow.

    表超过 MAX_TRAFFIC_ROWS 后删掉最旧的记录。
    每 PRUNE_EVERY 条插入才检查一次，把 COUNT(*) 的开销摊开，
    而不是每条流都在代理事件循环上付一次。
    """
    _since_prune[0] += 1
    if _since_prune[0] < PRUNE_EVERY:
        return
    _since_prune[0] = 0
    try:
        count = conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]
        if count <= MAX_TRAFFIC_ROWS:
            return
        conn.execute(
            """
            DELETE FROM traffic WHERE id IN (
                SELECT id FROM traffic ORDER BY timestamp ASC LIMIT ?
            )
            """,
            (count - MAX_TRAFFIC_ROWS,),
        )
        conn.commit()
    except Exception as e:
        print(f"Error pruning traffic: {e}")


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


async def request(flow):
    """在请求阶段检查 mock 规则，匹配则直接返回 mock 响应"""
    if _is_connect_tunnel(flow):
        return
    _strip_websocket_extensions(flow)
    await _throttle_upload(flow)

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
    await _throttle_download(flow, len(body))
    
    flow.response = http.Response.make(
        mock_rule.get("status_code", 200),
        body,
        headers,
    )
    
    increment_mock_hit(mock_rule["id"])
    
    counter[0] += 1
    print(f"[{counter[0]}] [MOCK] {method} {url[:80]} -> {mock_rule.get('status_code', 200)} (rule: {mock_rule['name']})")


async def responseheaders(flow):
    """
    Decide how the body is forwarded, before any of it arrives.

    Three cases: a WSS handshake must never stream; a throttled device flow is
    buffered so the pacing delay can be awaited off the event loop; everything
    else is teed — forwarded chunk by chunk while a capped copy is kept for the
    traffic table. Bodies we never store (images, media, fonts) pass straight
    through with no copy at all.

    在 body 到达前决定转发方式。
    三种情况：WSS 握手绝不能 stream；被限速的设备流走缓冲，好在事件循环外 await
    出限速延迟；其余一律 tee——逐块转发的同时留一份有上限的副本给流量表。
    本来就不入库的（图片 / 音视频 / 字体）纯透传，不做任何拷贝。
    """
    if _is_connect_tunnel(flow) or not getattr(flow, "response", None):
        return
    req_headers = _flow_headers(flow.request.headers)
    res_headers = _flow_headers(flow.response.headers)
    if is_websocket_handshake(req_headers):
        _delete_header_ci(flow.response.headers, "sec-websocket-extensions")
        return

    # A throttled flow must stay buffered: the stream callback is invoked
    # synchronously by the proxy layer and cannot await, so pacing there would
    # block every other connection. mitmproxy still force-streams bodies past
    # stream_large_bodies, which therefore escape pacing.
    #
    # 被限速的流必须保持缓冲：stream 回调由代理层同步调用、无法 await，在里面
    # 限速会阻塞其他所有连接。超过 stream_large_bodies 的 body 仍会被 mitmproxy
    # 强制流式转发，因而不受限速。
    if throttle_applies(flow) is not None:
        return

    content_type = _header_ci(res_headers, "content-type")
    if should_stream_body(content_type, flow.request.pretty_url, req_headers):
        flow.response.stream = True
    else:
        flow.response.stream = _make_tee_stream(flow)


async def response(flow):
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
        res_raw, wire_size = _response_body_for_store(flow, res_headers, store_res)
        req_raw = flow.request.content if store_req else None

        # Throttled flows are buffered (see responseheaders), so the body is
        # still in hand here and the wait happens before it reaches the client.
        #
        # 被限速的流是缓冲的（见 responseheaders），此刻 body 还在手上，
        # 等待发生在它到达客户端之前。
        await _throttle_download(flow)

        if resource_type in ("Other", "XHR") and looks_like_html(res_raw):
            resource_type = "Document"
        res_body, size = _store_payload(res_headers, res_raw, store_res)
        # 截断入库时 size 仍要反映线上真实字节数，列表里的大小才不会缩水。
        if wire_size > size:
            size = wire_size
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
        _prune_traffic(conn)
        is_mock = _header_ci(res_headers, "x-mock-rule")
        tag = " [MOCK]" if is_mock else ""
        print(f"[{counter[0]}]{tag} {flow.request.method} {url[:80]}")
    except Exception as e:
        print(f"Error saving traffic: {e}")


async def websocket_message(flow):
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
    config = throttle_applies(flow)
    if config is not None:
        bps = config.upload_bps if from_client else config.download_bps
        await _sleep(seconds_for_bytes(len(content), bps))
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
        _prune_traffic(conn)
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
