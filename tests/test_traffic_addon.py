"""mitmproxy addon 模块测试"""

import importlib
import os
import sqlite3
from pathlib import Path

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

    def test_exact(self, addon):
        assert addon.match_url("https://a.com/x", "https://a.com/x", "exact") is True
        assert addon.match_url("https://a.com/xy", "https://a.com/x", "exact") is False

    def test_regex(self, addon):
        assert addon.match_url("https://a.com/v1/pay/go", r"/v1/pay/.*", "regex") is True

    def test_invalid_regex_does_not_raise(self, addon):
        """规则里写了坏正则不能把整个代理搞崩"""
        assert addon.match_url("https://a.com/x", "[unclosed", "regex") is False
