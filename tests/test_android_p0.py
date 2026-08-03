"""Android P0 抓包能力测试"""

from unittest.mock import AsyncMock, patch

import pytest

from mitm_proxy_mcp.android.adb_client import ADBError
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


class TestAndroidCertStatus:
    """检测证书所在的凭据库"""

    @pytest.fixture
    def mock_adb(self):
        adb = AsyncMock()
        with patch.object(android_tools, "_get_adb", return_value=adb):
            yield adb

    @pytest.fixture
    def mock_cert(self):
        """固定证书文件名，避免依赖本机是否已生成 mitmproxy CA"""
        with patch.object(android_tools, "CertHelper") as MockHelper:
            MockHelper.return_value.get_cert_info.return_value.filename = "c8750f0b.0"
            yield MockHelper

    async def test_system_store_present_means_trusted(self, mock_adb, mock_cert):
        """证书在系统库 → App 信任"""
        mock_adb.get_android_version.return_value = 33
        mock_adb.is_rooted.return_value = True

        async def fake_shell(serial, cmd, **kwargs):
            if "/system/etc/security/cacerts/c8750f0b.0" in cmd:
                return (0, "EXISTS")
            return (1, "")

        mock_adb.shell_with_exit_code.side_effect = fake_shell

        result = await android_tools.android_cert_status("serial-1")

        assert result["success"] is True
        assert result["stores"]["system"] == "present"
        assert result["trusted_by_apps"] is True

    async def test_user_store_unknown_when_not_rooted(self, mock_adb, mock_cert):
        """未 root 时读不到用户库，必须返回 unknown 而不是 absent"""
        mock_adb.get_android_version.return_value = 34
        mock_adb.is_rooted.return_value = False
        mock_adb.shell_with_exit_code.return_value = (1, "Permission denied")

        result = await android_tools.android_cert_status("serial-1")

        assert result["stores"]["user"] == "unknown"
        assert result["trusted_by_apps"] is False

    async def test_user_store_unknown_on_silent_failure_when_not_rooted(
        self, mock_adb, mock_cert
    ):
        """未 root 时 test -f 静默失败（无 Permission denied）也应返回 unknown"""
        mock_adb.get_android_version.return_value = 34
        mock_adb.is_rooted.return_value = False
        mock_adb.shell_with_exit_code.return_value = (1, "")

        result = await android_tools.android_cert_status("serial-1")

        assert result["stores"]["user"] == "unknown"

    async def test_apex_store_checked_on_android_14(self, mock_adb, mock_cert):
        """Android 14+ 系统证书在 APEX 路径"""
        mock_adb.get_android_version.return_value = 34
        mock_adb.is_rooted.return_value = True

        async def fake_shell(serial, cmd, **kwargs):
            if "/apex/com.android.conscrypt/cacerts/c8750f0b.0" in cmd:
                return (0, "EXISTS")
            return (1, "")

        mock_adb.shell_with_exit_code.side_effect = fake_shell

        result = await android_tools.android_cert_status("serial-1")

        assert result["stores"]["apex"] == "present"
        assert result["trusted_by_apps"] is True

    async def test_advice_mentions_root_when_only_user_store(self, mock_adb, mock_cert):
        """只在用户库时要给出可执行的建议"""
        mock_adb.get_android_version.return_value = 33
        mock_adb.is_rooted.return_value = True

        async def fake_shell(serial, cmd, **kwargs):
            if "cacerts-added/c8750f0b.0" in cmd:
                return (0, "EXISTS")
            return (1, "")

        mock_adb.shell_with_exit_code.side_effect = fake_shell

        result = await android_tools.android_cert_status("serial-1")

        assert result["stores"]["user"] == "present"
        assert result["trusted_by_apps"] is False
        assert "android_inject_system_cert" in result["advice"]

    async def test_cert_missing_locally(self, mock_adb):
        """本机还没生成 mitmproxy CA"""
        with patch.object(android_tools, "CertHelper") as MockHelper:
            MockHelper.return_value.get_cert_info.side_effect = FileNotFoundError(
                "no cert"
            )

            result = await android_tools.android_cert_status("serial-1")

        assert result["success"] is False
        assert "代理" in result["message"]


class TestAndroidPushCert:
    """推送证书到设备"""

    @pytest.fixture
    def mock_adb(self):
        adb = AsyncMock()
        with patch.object(android_tools, "_get_adb", return_value=adb):
            yield adb

    async def test_push_returns_remote_path(self, mock_adb):
        """推送成功后返回设备上的路径和安装指引"""
        with patch.object(android_tools, "CertHelper") as MockHelper:
            helper = MockHelper.return_value
            helper.push_cert_to_device = AsyncMock(
                return_value="/sdcard/Download/c8750f0b.0"
            )
            helper.get_cert_info.return_value.filename = "c8750f0b.0"
            helper.get_install_instructions.return_value = "安装步骤"

            result = await android_tools.android_push_cert("serial-1")

        assert result["success"] is True
        assert result["remote_path"] == "/sdcard/Download/c8750f0b.0"
        assert result["cert_filename"] == "c8750f0b.0"
        assert result["instructions"] == "安装步骤"

    async def test_cert_missing_locally(self, mock_adb):
        """本机没有 CA 证书时给出明确提示"""
        with patch.object(android_tools, "CertHelper") as MockHelper:
            MockHelper.return_value.get_cert_info.side_effect = FileNotFoundError("x")

            result = await android_tools.android_push_cert("serial-1")

        assert result["success"] is False
        assert "代理" in result["message"]


class TestAndroidInjectSystemCert:
    """注入系统凭据库"""

    @pytest.fixture
    def mock_adb(self):
        adb = AsyncMock()
        with patch.object(android_tools, "_get_adb", return_value=adb):
            yield adb

    @pytest.fixture
    def mock_cert(self):
        with patch.object(android_tools, "CertHelper") as MockHelper:
            helper = MockHelper.return_value
            helper.get_cert_info.return_value.filename = "c8750f0b.0"
            helper.push_cert_to_device = AsyncMock(
                return_value="/sdcard/Download/c8750f0b.0"
            )
            yield MockHelper

    async def test_requires_root(self, mock_adb, mock_cert):
        """未 root 时不该去尝试 mount，直接给出可执行的替代方案"""
        mock_adb.is_rooted.return_value = False
        mock_adb.get_android_version.return_value = 34

        result = await android_tools.android_inject_system_cert("serial-1")

        assert result["success"] is False
        assert "root" in result["message"]
        mock_adb.root_shell.assert_not_called()

    async def test_legacy_remount_on_sdk_33(self, mock_adb, mock_cert):
        """Android 13 及以下走 /system remount"""
        mock_adb.is_rooted.return_value = True
        mock_adb.get_android_version.return_value = 33
        mock_adb.root_shell.return_value = (0, "")

        result = await android_tools.android_inject_system_cert("serial-1")

        assert result["success"] is True
        assert result["method"] == "system_remount"
        assert result["persistent"] is True
        executed = " ".join(c.args[1] for c in mock_adb.root_shell.call_args_list)
        assert "remount" in executed
        assert "/system/etc/security/cacerts/c8750f0b.0" in executed

    async def test_apex_tmpfs_on_sdk_34(self, mock_adb, mock_cert):
        """Android 14+ 走 APEX tmpfs 覆盖，并声明不持久"""
        mock_adb.is_rooted.return_value = True
        mock_adb.get_android_version.return_value = 34
        mock_adb.root_shell.return_value = (0, "")

        result = await android_tools.android_inject_system_cert("serial-1")

        assert result["success"] is True
        assert result["method"] == "apex_tmpfs"
        assert result["persistent"] is False
        assert "重启" in result["warning"]
        executed = " ".join(c.args[1] for c in mock_adb.root_shell.call_args_list)
        assert "tmpfs" in executed

    async def test_mount_failure_surfaces_output(self, mock_adb, mock_cert):
        """mount 失败要把设备的原始输出带回来，否则没法排查"""
        mock_adb.is_rooted.return_value = True
        mock_adb.get_android_version.return_value = 33
        mock_adb.root_shell.return_value = (1, "mount: Read-only file system")

        result = await android_tools.android_inject_system_cert("serial-1")

        assert result["success"] is False
        assert "Read-only file system" in result["message"]


class TestAndroidReverseProxy:
    """adb reverse 方式接入代理"""

    @pytest.fixture
    def mock_adb(self):
        adb = AsyncMock()
        with patch.object(android_tools, "_get_adb", return_value=adb):
            yield adb

    async def test_sets_reverse_then_local_proxy(self, mock_adb):
        """先建立 reverse 隧道，再把设备代理指向 127.0.0.1"""
        mock_adb.reverse.return_value = True
        mock_adb.shell_with_exit_code.return_value = (0, "")

        result = await android_tools.android_reverse_proxy("serial-1", port=8888)

        assert result["success"] is True
        assert result["proxy"] == "127.0.0.1:8888"
        mock_adb.reverse.assert_awaited_once_with("serial-1", "tcp:8888", "tcp:8888")
        cmd = mock_adb.shell_with_exit_code.call_args.args[1]
        assert "settings put global http_proxy 127.0.0.1:8888" in cmd

    async def test_reverse_failure_does_not_set_proxy(self, mock_adb):
        """隧道没建起来就不能改设备代理，否则设备会彻底断网"""
        mock_adb.reverse.side_effect = ADBError("device offline")

        result = await android_tools.android_reverse_proxy("serial-1")

        assert result["success"] is False
        mock_adb.shell_with_exit_code.assert_not_called()

    async def test_remove_clears_both(self, mock_adb):
        """移除时要同时清掉代理设置和隧道"""
        mock_adb.reverse_remove.return_value = True
        mock_adb.shell_with_exit_code.return_value = (0, "")

        result = await android_tools.android_reverse_proxy_remove("serial-1", port=8888)

        assert result["success"] is True
        mock_adb.reverse_remove.assert_awaited_once_with("serial-1", "tcp:8888")
        cmd = mock_adb.shell_with_exit_code.call_args.args[1]
        assert "http_proxy :0" in cmd
