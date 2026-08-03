import json
import re
import time
from pathlib import Path
import sqlite3
from urllib.parse import urlparse
from mitmproxy import http

import os

# Paths come from the environment so this module stays importable — and
# therefore testable — instead of being generated as an f-string at runtime.
#
# 路径改从环境变量读取，这样本模块可以被 import（因而可测），
# 而不是运行时用 f-string 拼出来。
DB_PATH = os.environ.get("MITMPROXY_DB_PATH", "/tmp/mitmproxy-traffic.db")
MOCK_DB_PATH = os.environ.get("MITMPROXY_MOCK_DB_PATH", "/tmp/mitmproxy-mock.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()

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
    if "json" in mime_lower or "xml" in mime_lower or ext in (".json", ".xml"):
        return "XHR"
    if mime_lower.startswith("application/"):
        return "XHR"
    
    return "Other"

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
            regex_pattern = re.escape(pattern).replace(r"\\*", ".*")
            try:
                return bool(re.search(regex_pattern, url))
            except re.error:
                return False
        return pattern in url

def find_matching_mock(url, method):
    """查找匹配的 mock 规则"""
    if not Path(MOCK_DB_PATH).exists():
        return None
    try:
        conn = sqlite3.connect(MOCK_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM mock_rules WHERE enabled = 1 ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        
        for row in rows:
            rule_method = row["method"] or ""
            if rule_method and rule_method != method.upper():
                continue
            if match_url(url, row["url_pattern"], row["match_type"] or "contains"):
                return dict(row)
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

counter = [0]

def request(flow):
    """在请求阶段检查 mock 规则，匹配则直接返回 mock 响应"""
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
    
    flow.response = http.Response.make(
        mock_rule.get("status_code", 200),
        body,
        headers,
    )
    
    increment_mock_hit(mock_rule["id"])
    
    counter[0] += 1
    print(f"[{counter[0]}] [MOCK] {method} {url[:80]} -> {mock_rule.get('status_code', 200)} (rule: {mock_rule['name']})")

def response(flow):
    counter[0] += 1
    record_id = f"req-{counter[0]}"

    if not flow.response:
        return
    
    try:
        url = flow.request.pretty_url
        domain = flow.request.host
        
        content_type = flow.response.headers.get("content-type", "")
        resource_type = infer_resource_type(content_type, url)
        
        if flow.response.timestamp_end and flow.request.timestamp_start:
            time_ms = (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
        else:
            time_ms = 0.0

        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO traffic (
                    id, timestamp, method, url, domain, status,
                    resource_type, size, time_ms, request_headers,
                    request_body, response_headers, response_body, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                time.time(),
                flow.request.method,
                url,
                domain,
                flow.response.status_code,
                resource_type,
                len(flow.response.content) if flow.response.content else 0,
                time_ms,
                json.dumps(dict(flow.request.headers)),
                flow.request.content,
                json.dumps(dict(flow.response.headers)),
                flow.response.content,
                None
            ))
            conn.commit()
            is_mock = flow.response.headers.get("X-Mock-Rule", "")
            tag = " [MOCK]" if is_mock else ""
            print(f"[{counter[0]}]{tag} {flow.request.method} {url[:80]}")
        finally:
            conn.close()
    except Exception as e:
        print(f"Error saving traffic: {e}")


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

        conn = sqlite3.connect(DB_PATH)
        try:
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
        finally:
            conn.close()

        print(f"[{counter[0]}] [TLS-FAIL] {sni} -> {error}")

    except Exception as e:
        print(f"Error recording TLS failure: {e}")


init_db()
