"""mitmproxy addon 模块测试"""

import importlib
import sqlite3

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
