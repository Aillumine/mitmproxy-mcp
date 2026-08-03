"""Android P0 抓包能力测试"""

from unittest.mock import AsyncMock, patch

import pytest

from mitm_proxy_mcp.tools import android_tools


class TestAndroidGetProxy:
    """读取设备代理设置"""

    @pytest.fixture
    def mock_adb(self):
        """替换模块级 ADB 单例，避免真的去找 adb 可执行文件"""
        adb = AsyncMock()
        with patch.object(android_tools, "_get_adb", return_value=adb):
            yield adb

    async def test_returns_host_and_port_when_set(self, mock_adb):
        """已设置代理时拆出 host 与 port"""
        mock_adb.shell_with_exit_code.return_value = (0, "192.168.1.24:8888")

        result = await android_tools.android_get_proxy("serial-1")

        assert result["success"] is True
        assert result["proxy"] == "192.168.1.24:8888"
        assert result["host"] == "192.168.1.24"
        assert result["port"] == 8888

    async def test_colon_zero_means_not_set(self, mock_adb):
        """Android 用 :0 表示未设置代理，不能当成 host 为空的代理"""
        mock_adb.shell_with_exit_code.return_value = (0, ":0")

        result = await android_tools.android_get_proxy("serial-1")

        assert result["success"] is True
        assert result["proxy"] is None

    async def test_null_means_not_set(self, mock_adb):
        """settings 未写过该键时返回字符串 null"""
        mock_adb.shell_with_exit_code.return_value = (0, "null")

        result = await android_tools.android_get_proxy("serial-1")

        assert result["success"] is True
        assert result["proxy"] is None

    async def test_nonzero_exit_code_fails(self, mock_adb):
        """设备离线等情况下 exit code 非 0"""
        mock_adb.shell_with_exit_code.return_value = (1, "device offline")

        result = await android_tools.android_get_proxy("serial-1")

        assert result["success"] is False
        assert "device offline" in result["message"]
