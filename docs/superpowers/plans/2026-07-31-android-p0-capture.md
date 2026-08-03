# Android P0 抓包能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Android 抓包的 5 个 P0 MCP 工具（读代理、查证书 store、推证书、注入系统证书、adb reverse），并让 TLS 握手失败的连接进入流量库，使「抓不到包」从猜测变成可诊断。

**Architecture:** 前 5 个工具是纯增量——在 `tools/android_tools.py` 里新增函数，包装 `ADBClient` 与 `CertHelper` 里**已经实现但从未暴露**的能力，再注册到 `server.py` 的 `list_tools()` 与 `call_tool()`。第 6 项要改 mitmproxy addon，而当前 addon 是内嵌在 `cli/start.py` 里的 200 行 f-string，无法测试，因此先做一次行为不变的重构把它抽成真实模块，再加 TLS 失败记录。

**Tech Stack:** Python 3.11+ / mcp 1.x / mitmproxy 11.0.2 / pytest + pytest-asyncio / unittest.mock / ruff

## Global Constraints

- Python `>=3.11`，ruff `line-length = 88`，`target-version = "py311"`，lint 规则 `["E", "F", "I", "UP"]`
- **不新增第三方依赖**。只用 stdlib 与已有的 `mitmproxy` / `mcp` / `loguru`
- pytest 配置已有 `asyncio_mode = "auto"`，异步测试**不要**加 `@pytest.mark.asyncio`
- 测试放 `tests/`，风格对齐现有文件：class 分组、中文 docstring、`unittest.mock` 的 `patch` / `AsyncMock`
- 所有新工具返回 `dict[str, Any]` 且**必须**含 `success: bool`，失败时含 `message: str`（中文），与现有 26 个工具保持一致
- 需要真机/root 的测试用已注册的 marker：`requires_device`、`requires_rooted_device`
- 注释用双语：英文段 + 空注释行 `#` + 中文段
- commit message 不得包含任何 AI 署名、`Co-Authored-By` 或 `Generated with` 页脚
- 证书文件名统一由 `CertHelper().get_cert_info().filename` 得到（形如 `c8750f0b.0`），**不要**自己拼 hash

## 已确认的现状（实现时不要再去猜）

| 事实 | 位置 |
|---|---|
| 证书助手类名是 **`CertHelper`**，不是 `CertInjector` | `src/mitm_proxy_mcp/android/cert_injector.py:25` |
| `CertHelper.push_cert_to_device(serial, cert_path=None) -> str` 已实现，未暴露 | `android/cert_injector.py:79` |
| `ADBClient.reverse(serial, remote, local) -> bool` 已实现，未暴露 | `android/adb_client.py:412` |
| `ADBClient.root_shell(serial, command, timeout=30.0) -> tuple[int, str]` 已实现 | `android/adb_client.py:339` |
| `ADBClient.shell_with_exit_code(serial, command, timeout=30.0) -> tuple[int, str]` 返回真实 exit code | `android/adb_client.py:187` |
| `ADBClient.shell()` 返回的是 **adb 进程**的 returncode，不是设备命令的 —— 判断命令成败必须用 `shell_with_exit_code` | `android/adb_client.py:181-183` |
| `ADBClient.get_android_version(serial) -> int` 返回 SDK 号（34 = Android 14） | `android/adb_client.py:363` |
| `_get_adb()` 是模块级单例工厂 | `tools/android_tools.py:15` |
| addon 是 f-string，只有 `{db_path}` `{mock_db_path}` 两个真插值点，其余 `{}` 全被迫写成 `{{}}` | `cli/start.py:371-573` |
| mitmproxy 11.0.2 的钩子为 `tls_failed_client(data)`，`data.conn` 带 `error`/`sni` | 已实机确认 |

---

### Task 1: `android_get_proxy` — 读取设备当前代理

自检第 2 步「设备代理已配置」需要它。现在只有 `android_setup_proxy` 能写，没有任何工具能读。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`（文件末尾追加）
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`（`list_tools()` 的 Android 段、`call_tool()` 的 Android 段）
- Test: `tests/test_android_p0.py`（新建）

**Interfaces:**
- Consumes: `ADBClient.shell_with_exit_code`、`android_tools._get_adb`
- Produces: `async android_get_proxy(serial: str) -> dict` — 键为 `success: bool`、`proxy: str | None`、`host: str | None`、`port: int | None`、`raw: str`。未设置代理时 `proxy` 为 `None` 且 `success` 仍为 `True`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_android_p0.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：`AttributeError: module 'mitm_proxy_mcp.tools.android_tools' has no attribute 'android_get_proxy'`

- [ ] **Step 3: 实现**

在 `src/mitm_proxy_mcp/tools/android_tools.py` 末尾追加：

```python
async def android_get_proxy(serial: str) -> dict[str, Any]:
    """
    读取设备当前的全局代理设置

    Args:
        serial: 设备序列号

    Returns:
        包含代理设置的字典，未设置代理时 proxy 为 None
    """
    try:
        adb = _get_adb()

        # shell_with_exit_code rather than shell: the latter returns the adb
        # process exit code, which is 0 even when the on-device command fails.
        #
        # 用 shell_with_exit_code 而非 shell：后者返回的是 adb 进程的退出码，
        # 设备上的命令失败时它依然是 0，无法用来判断成败。
        exit_code, output = await adb.shell_with_exit_code(
            serial, "settings get global http_proxy"
        )
        raw = output.strip()

        if exit_code != 0:
            return {"success": False, "message": f"读取代理设置失败: {raw}", "raw": raw}

        # Android writes ":0" or "null" when no proxy is configured; treating
        # either as a real proxy would make the self-check report a false pass.
        #
        # 未配置代理时 Android 写入的是 ":0" 或 "null"，若当成真代理，
        # 连接自检会误报「已配置」。
        if raw in ("", ":0", "null"):
            return {
                "success": True,
                "proxy": None,
                "host": None,
                "port": None,
                "raw": raw,
            }

        host, _, port_str = raw.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            host, port = raw, None

        return {
            "success": True,
            "proxy": raw,
            "host": host or None,
            "port": port,
            "raw": raw,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：4 passed

- [ ] **Step 5: 导出并注册到 MCP**

在 `src/mitm_proxy_mcp/tools/__init__.py` 的 android 导入块加入 `android_get_proxy`，并加进 `__all__`。

在 `server.py` 顶部 `from .tools import (...)` 里加 `android_get_proxy,`。

在 `list_tools()` 的 `android_clear_proxy` 定义之后插入：

```python
        Tool(
            name="android_get_proxy",
            description="读取 Android 设备当前的全局代理设置（用于诊断抓不到包的原因）",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
```

在 `call_tool()` 的 `android_clear_proxy` 分支之后插入：

```python
    elif name == "android_get_proxy":
        result = await android_get_proxy(arguments["serial"])
```

- [ ] **Step 6: 验证工具已注册**

```bash
uv run python -c "
import asyncio
from mitm_proxy_mcp.server import list_tools, call_tool
names = [t.name for t in asyncio.run(list_tools())]
assert 'android_get_proxy' in names, names
print('已注册，工具总数', len(names))
"
```

预期：`已注册，工具总数 27`

- [ ] **Step 7: 跑全量测试与 lint**

```bash
uv run pytest -q && uv run ruff check src tests
```

预期：全部通过，无 lint 报错

- [ ] **Step 8: 提交**

```bash
git add src/mitm_proxy_mcp/tools/android_tools.py src/mitm_proxy_mcp/tools/__init__.py src/mitm_proxy_mcp/server.py tests/test_android_p0.py
git commit -m "feat(android): 新增 android_get_proxy 读取设备代理设置"
```

---

### Task 2: `android_cert_status` — 检测 CA 证书在哪个凭据库

Android 抓包最大的坑：Android 7+ 的 App 默认只信任系统凭据库。这个工具要能明确回答「证书装了没、装在哪、App 会不会信」。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`
- Test: `tests/test_android_p0.py`

**Interfaces:**
- Consumes: `CertHelper.get_cert_info()`（拿 `filename`）、`ADBClient.shell_with_exit_code`、`ADBClient.get_android_version`、`ADBClient.is_rooted`
- Produces: `async android_cert_status(serial: str) -> dict` — 键为 `success`、`cert_filename`、`sdk_version`、`is_rooted`、`stores: dict[str, str]`（三个库各自取值 `present` / `absent` / `unknown`）、`trusted_by_apps: bool`、`advice: str`

**关键约束：** `/data/misc/user/0/cacerts-added` 普通 shell **无读权限**，未 root 设备只能返回 `unknown`，不许猜成 `absent`——否则会误导用户去重复安装。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_android_p0.py`：

```python
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
            MockHelper.return_value.get_cert_info.side_effect = FileNotFoundError("no cert")

            result = await android_tools.android_cert_status("serial-1")

        assert result["success"] is False
        assert "代理" in result["message"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_android_p0.py::TestAndroidCertStatus -v
```

预期：`AttributeError: ... has no attribute 'android_cert_status'`

- [ ] **Step 3: 实现**

先在 `android_tools.py` 顶部的 import 区加入：

```python
from ..android.cert_injector import CertHelper
```

然后在文件末尾追加：

```python
# Certificate store paths. Android 14 (SDK 34) moved the system store into the
# Conscrypt APEX, so the legacy path alone is no longer conclusive.
#
# 证书凭据库路径。Android 14（SDK 34）起系统库迁到了 Conscrypt APEX，
# 只查旧路径已经不足以判断。
_USER_STORE = "/data/misc/user/0/cacerts-added"
_SYSTEM_STORE = "/system/etc/security/cacerts"
_APEX_STORE = "/apex/com.android.conscrypt/cacerts"


async def _probe_cert(adb, serial: str, store: str, filename: str) -> str:
    """
    探测某个凭据库里是否存在指定证书

    Returns:
        present | absent | unknown（无读权限时为 unknown）
    """
    path = f"{store}/{filename}"
    exit_code, output = await adb.shell_with_exit_code(
        serial, f"test -f {path} && echo EXISTS"
    )

    if exit_code == 0 and "EXISTS" in output:
        return "present"

    # A permission error is not the same as "no certificate": the user store is
    # unreadable without root, and reporting absent there would tell the user to
    # install a certificate that may already be installed.
    #
    # 权限错误不等于「没有证书」：用户库无 root 读不了，误报 absent 会让用户
    # 去重复安装一个可能已经装好的证书。
    if "Permission denied" in output or "denied" in output.lower():
        return "unknown"

    return "absent"


async def android_cert_status(serial: str) -> dict[str, Any]:
    """
    检测 mitmproxy CA 证书在设备上的安装状态

    Args:
        serial: 设备序列号

    Returns:
        包含三个凭据库状态、是否被 App 信任、以及处理建议的字典
    """
    try:
        cert_info = CertHelper().get_cert_info()
    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 mitmproxy CA 证书。请先启动代理以生成证书。",
        }

    filename = cert_info.filename

    try:
        adb = _get_adb()
        sdk_version = await adb.get_android_version(serial)
        is_rooted = await adb.is_rooted(serial)

        stores = {
            "user": await _probe_cert(adb, serial, _USER_STORE, filename),
            "system": await _probe_cert(adb, serial, _SYSTEM_STORE, filename),
            "apex": await _probe_cert(adb, serial, _APEX_STORE, filename),
        }

        # Only the system stores make apps trust the CA. A certificate sitting in
        # the user store is invisible to any app targeting Android 7+ unless that
        # app opted in via networkSecurityConfig.
        #
        # 只有系统库能让 App 信任 CA。装在用户库里的证书，对任何
        # targetSdk >= 24 的 App 都是不可见的，除非该 App 主动在
        # networkSecurityConfig 里声明信任用户证书。
        trusted = stores["system"] == "present" or stores["apex"] == "present"

        if trusted:
            advice = "证书已在系统凭据库，App 会信任该 CA。"
        elif stores["user"] == "present":
            advice = (
                "证书只在用户凭据库，Android 7+ 的 App 默认不信任。"
                "请调用 android_inject_system_cert 注入系统库"
                "（需要 root 或使用模拟器）。"
            )
        elif stores["user"] == "unknown":
            advice = (
                "设备未 root，无法读取用户凭据库状态。"
                "若确认未安装，请先调用 android_push_cert 推送证书后手动安装，"
                "再用 android_inject_system_cert 注入系统库。"
            )
        else:
            advice = (
                "设备上未安装证书。请先调用 android_push_cert 推送到 "
                "/sdcard/Download，再在系统设置里安装。"
            )

        return {
            "success": True,
            "cert_filename": filename,
            "sdk_version": sdk_version,
            "is_rooted": is_rooted,
            "stores": stores,
            "trusted_by_apps": trusted,
            "advice": advice,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：9 passed

- [ ] **Step 5: 注册到 MCP**

`tools/__init__.py` 导出 `android_cert_status`；`server.py` 顶部导入。

`list_tools()` 里追加：

```python
        Tool(
            name="android_cert_status",
            description="检测 mitmproxy CA 证书在 Android 设备上的安装状态（用户库/系统库/APEX），并判断 App 是否会信任",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
```

`call_tool()` 里追加：

```python
    elif name == "android_cert_status":
        result = await android_cert_status(arguments["serial"])
```

- [ ] **Step 6: 验证并提交**

```bash
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "feat(android): 新增 android_cert_status 检测证书凭据库状态"
```

---

### Task 3: `android_push_cert` — 推送 CA 证书到设备

`CertHelper.push_cert_to_device()` 已经写好但从未暴露。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`
- Test: `tests/test_android_p0.py`

**Interfaces:**
- Consumes: `CertHelper(adb).push_cert_to_device(serial) -> str`、`CertHelper.get_install_instructions()`
- Produces: `async android_push_cert(serial: str) -> dict` — 键为 `success`、`remote_path`、`cert_filename`、`instructions`

- [ ] **Step 1: 写失败的测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_android_p0.py::TestAndroidPushCert -v
```

预期：`AttributeError: ... 'android_push_cert'`

- [ ] **Step 3: 实现**

```python
async def android_push_cert(serial: str) -> dict[str, Any]:
    """
    推送 mitmproxy CA 证书到设备的 /sdcard/Download

    推送只是第一步，用户仍需在系统设置里手动安装，或调用
    android_inject_system_cert 注入系统凭据库。

    Args:
        serial: 设备序列号

    Returns:
        包含设备路径与安装指引的字典
    """
    try:
        adb = _get_adb()
        helper = CertHelper(adb)

        cert_info = helper.get_cert_info()
        remote_path = await helper.push_cert_to_device(serial)

        return {
            "success": True,
            "remote_path": remote_path,
            "cert_filename": cert_info.filename,
            "instructions": helper.get_install_instructions(cert_info),
        }

    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 mitmproxy CA 证书。请先启动代理以生成证书。",
        }
    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：11 passed

- [ ] **Step 5: 注册到 MCP**

`list_tools()`：

```python
        Tool(
            name="android_push_cert",
            description="推送 mitmproxy CA 证书到 Android 设备的 /sdcard/Download 并返回安装指引",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
```

`call_tool()`：

```python
    elif name == "android_push_cert":
        result = await android_push_cert(arguments["serial"])
```

- [ ] **Step 6: 验证并提交**

```bash
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "feat(android): 新增 android_push_cert 推送 CA 证书到设备"
```

---

### Task 4: `android_inject_system_cert` — 注入系统凭据库

分两条路径：SDK ≤ 33 走传统 `/system` remount；SDK ≥ 34 走 Conscrypt APEX 的 tmpfs 覆盖。

**诚实声明（要写进工具返回值，不许省略）：** APEX 方案是内存覆盖，**设备重启后失效**；且它只对在挂载之后启动的进程生效，已在运行的 App 需要重启。这个限制必须由工具本身告知调用方，不能让用户自己去踩。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`
- Test: `tests/test_android_p0.py`

**Interfaces:**
- Consumes: `ADBClient.is_rooted`、`ADBClient.get_android_version`、`ADBClient.root_shell`、`android_push_cert`
- Produces: `async android_inject_system_cert(serial: str) -> dict` — 键为 `success`、`method`（`"system_remount"` / `"apex_tmpfs"`）、`persistent: bool`、`message`、`warning`

- [ ] **Step 1: 写失败的测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_android_p0.py::TestAndroidInjectSystemCert -v
```

预期：`AttributeError: ... 'android_inject_system_cert'`

- [ ] **Step 3: 实现**

```python
async def _run_root_steps(adb, serial: str, commands: list[str]) -> tuple[bool, str]:
    """
    按顺序执行一组 root 命令，任一失败即中断

    Returns:
        (是否全部成功, 失败时的设备输出)
    """
    for command in commands:
        exit_code, output = await adb.root_shell(serial, command)
        if exit_code != 0:
            return False, output.strip()
    return True, ""


async def android_inject_system_cert(serial: str) -> dict[str, Any]:
    """
    把 mitmproxy CA 证书注入设备的系统凭据库

    Android 13 及以下重新挂载 /system 写入，重启后仍然有效；
    Android 14+ 的系统库位于只读的 Conscrypt APEX，只能用 tmpfs 覆盖，
    重启后失效。

    Args:
        serial: 设备序列号

    Returns:
        包含注入方式、是否持久、以及限制说明的字典
    """
    try:
        cert_info = CertHelper().get_cert_info()
    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 mitmproxy CA 证书。请先启动代理以生成证书。",
        }

    filename = cert_info.filename

    try:
        adb = _get_adb()

        if not await adb.is_rooted(serial):
            return {
                "success": False,
                "message": (
                    "注入系统凭据库需要 root 权限，该设备未 root。"
                    "可改用 Android 模拟器（可写系统分区），"
                    "或在 App 的 debug 构建里通过 networkSecurityConfig 信任用户证书。"
                ),
            }

        sdk_version = await adb.get_android_version(serial)

        # Push first so the certificate exists on-device regardless of which
        # injection path we take below.
        #
        # 先推送，保证证书在设备上存在，两条注入路径都要用到它。
        helper = CertHelper(adb)
        remote_path = await helper.push_cert_to_device(serial)

        if sdk_version >= 34:
            # Android 14 moved the system trust store into a read-only APEX.
            # Overlaying it with tmpfs is the only root-only option, and it lives
            # in memory: a reboot drops it.
            #
            # Android 14 把系统信任库挪进了只读的 APEX。root 环境下只能用 tmpfs
            # 覆盖，而这个覆盖在内存里，重启即失效。
            commands = [
                f"mount -t tmpfs tmpfs {_APEX_STORE}",
                f"cp {_SYSTEM_STORE}/* {_APEX_STORE}/ 2>/dev/null || true",
                f"cp {remote_path} {_APEX_STORE}/{filename}",
                f"chmod 644 {_APEX_STORE}/{filename}",
                f"chown root:root {_APEX_STORE}/{filename}",
                f"chcon u:object_r:system_file:s0 {_APEX_STORE}/{filename}",
            ]
            ok, output = await _run_root_steps(adb, serial, commands)
            if not ok:
                return {"success": False, "message": f"APEX 注入失败: {output}"}

            return {
                "success": True,
                "method": "apex_tmpfs",
                "persistent": False,
                "message": f"证书已注入 {_APEX_STORE}/{filename}",
                "warning": (
                    "该覆盖位于内存，设备重启后失效，需要重新注入；"
                    "且只对挂载之后启动的进程生效，请重启目标 App。"
                ),
            }

        commands = [
            "mount -o rw,remount /system",
            f"cp {remote_path} {_SYSTEM_STORE}/{filename}",
            f"chmod 644 {_SYSTEM_STORE}/{filename}",
            f"chown root:root {_SYSTEM_STORE}/{filename}",
            "mount -o ro,remount /system",
        ]
        ok, output = await _run_root_steps(adb, serial, commands)
        if not ok:
            return {"success": False, "message": f"系统分区写入失败: {output}"}

        return {
            "success": True,
            "method": "system_remount",
            "persistent": True,
            "message": f"证书已注入 {_SYSTEM_STORE}/{filename}",
            "warning": "请重启目标 App 以让新的信任链生效。",
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：15 passed

- [ ] **Step 5: 注册到 MCP**

`list_tools()`：

```python
        Tool(
            name="android_inject_system_cert",
            description=(
                "把 mitmproxy CA 注入 Android 系统凭据库（需要 root）。"
                "Android 13 及以下重挂载 /system 持久生效；"
                "Android 14+ 用 APEX tmpfs 覆盖，重启后失效"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                },
                "required": ["serial"],
            },
        ),
```

`call_tool()`：

```python
    elif name == "android_inject_system_cert":
        result = await android_inject_system_cert(arguments["serial"])
```

- [ ] **Step 6: 验证并提交**

```bash
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "feat(android): 新增 android_inject_system_cert 注入系统凭据库"
```

---

### Task 5: `android_reverse_proxy` — 用 adb reverse 接入代理

比全局代理更稳：走 `127.0.0.1`，真机不需要和 Mac 同局域网。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`
- Test: `tests/test_android_p0.py`

**Interfaces:**
- Consumes: `ADBClient.reverse(serial, remote, local)`、`ADBClient.reverse_remove(serial, remote)`、`ADBClient.shell_with_exit_code`
- Produces:
  - `async android_reverse_proxy(serial: str, port: int = 8888) -> dict` — 键为 `success`、`proxy`、`remote`、`local`
  - `async android_reverse_proxy_remove(serial: str, port: int = 8888) -> dict` — 键为 `success`、`message`

**注意参数顺序：** `reverse(serial, remote, local)` 中 `remote` 是**设备侧**端口。两个都传 `tcp:{port}`，但顺序写反会导致行为不符合预期，测试要断言这一点。

- [ ] **Step 1: 写失败的测试**

```python
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
```

`ADBError` 需要在测试文件顶部导入：

```python
from mitm_proxy_mcp.android.adb_client import ADBError
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_android_p0.py::TestAndroidReverseProxy -v
```

预期：`AttributeError: ... 'android_reverse_proxy'`

- [ ] **Step 3: 实现**

```python
async def android_reverse_proxy(serial: str, port: int = 8888) -> dict[str, Any]:
    """
    通过 adb reverse 让设备经由 127.0.0.1 访问主机代理

    相比全局代理，这种方式不要求设备与主机在同一局域网，
    也不受 Wi-Fi 切换影响，是真机抓包更可靠的接法。

    Args:
        serial: 设备序列号
        port: 主机上的代理端口

    Returns:
        包含代理地址与隧道信息的字典
    """
    try:
        adb = _get_adb()
        endpoint = f"tcp:{port}"

        # Establish the tunnel before pointing the device at 127.0.0.1. Doing it
        # the other way round would leave the device with a proxy that routes
        # nowhere if the tunnel fails — i.e. no network at all.
        #
        # 必须先建隧道再改代理指向。反过来的话，一旦建隧道失败，设备的代理会
        # 指向一个不存在的本地端口，结果是整机断网。
        await adb.reverse(serial, endpoint, endpoint)

        exit_code, output = await adb.shell_with_exit_code(
            serial, f"settings put global http_proxy 127.0.0.1:{port}"
        )
        if exit_code != 0:
            return {"success": False, "message": f"设置代理失败: {output.strip()}"}

        return {
            "success": True,
            "proxy": f"127.0.0.1:{port}",
            "remote": endpoint,
            "local": endpoint,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}


async def android_reverse_proxy_remove(serial: str, port: int = 8888) -> dict[str, Any]:
    """
    移除 adb reverse 代理接入

    Args:
        serial: 设备序列号
        port: 主机上的代理端口

    Returns:
        包含移除状态的字典
    """
    try:
        adb = _get_adb()

        # Clear the proxy first: dropping the tunnel while the device still points
        # at 127.0.0.1 would black-hole its traffic.
        #
        # 先清代理再拆隧道：如果设备还指着 127.0.0.1 就先拆隧道，
        # 这段时间内设备流量会被黑洞掉。
        exit_code, output = await adb.shell_with_exit_code(
            serial, "settings put global http_proxy :0"
        )
        if exit_code != 0:
            return {"success": False, "message": f"清除代理失败: {output.strip()}"}

        await adb.reverse_remove(serial, f"tcp:{port}")

        return {"success": True, "message": f"已移除 reverse 代理（tcp:{port}）"}

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_android_p0.py -v
```

预期：18 passed

- [ ] **Step 5: 注册到 MCP**

`list_tools()`：

```python
        Tool(
            name="android_reverse_proxy",
            description=(
                "用 adb reverse 让 Android 设备经 127.0.0.1 访问主机代理，"
                "不要求设备与主机同局域网，比全局代理更稳"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                    "port": {
                        "type": "integer",
                        "description": "主机代理端口，默认 8888",
                    },
                },
                "required": ["serial"],
            },
        ),
        Tool(
            name="android_reverse_proxy_remove",
            description="移除 adb reverse 代理接入，恢复设备网络设置",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                    "port": {
                        "type": "integer",
                        "description": "主机代理端口，默认 8888",
                    },
                },
                "required": ["serial"],
            },
        ),
```

`call_tool()`：

```python
    elif name == "android_reverse_proxy":
        result = await android_reverse_proxy(
            serial=arguments["serial"],
            port=arguments.get("port", 8888),
        )
    elif name == "android_reverse_proxy_remove":
        result = await android_reverse_proxy_remove(
            serial=arguments["serial"],
            port=arguments.get("port", 8888),
        )
```

- [ ] **Step 6: 验证工具总数并提交**

```bash
uv run python -c "
import asyncio
from mitm_proxy_mcp.server import list_tools
names = [t.name for t in asyncio.run(list_tools())]
for n in ['android_get_proxy','android_cert_status','android_push_cert','android_inject_system_cert','android_reverse_proxy','android_reverse_proxy_remove']:
    assert n in names, n
print('工具总数', len(names))
"
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "feat(android): 新增 adb reverse 代理接入与移除工具"
```

预期：`工具总数 32`

---

### Task 6: 把 mitmproxy addon 抽成可测试模块（行为不变的重构）

Task 7 要给 addon 加 TLS 失败记录，但现在 addon 是 `cli/start.py:371-573` 里一段 200 行的 f-string——不能 import、不能测试、所有 `{}` 都被迫写成 `{{}}`。先做一次纯重构，不改任何行为。

**Files:**
- Create: `src/mitm_proxy_mcp/addon/__init__.py`
- Create: `src/mitm_proxy_mcp/addon/traffic_addon.py`
- Modify: `src/mitm_proxy_mcp/cli/start.py:370-577`
- Test: `tests/test_traffic_addon.py`（新建）

**Interfaces:**
- Consumes: 无（addon 独立运行在 mitmdump 进程内）
- Produces: 模块 `mitm_proxy_mcp.addon.traffic_addon`，导出 `init_db()`、`infer_resource_type(mime_type, url)`、`match_url(url, pattern, match_type)`、`find_matching_mock(url, method)`、`request(flow)`、`response(flow)`；数据库路径改从环境变量 `MITMPROXY_DB_PATH` / `MITMPROXY_MOCK_DB_PATH` 读取，缺省回落到 `/tmp/mitmproxy-traffic.db` 与 `/tmp/mitmproxy-mock.db`

- [ ] **Step 1: 原样抽出 addon 代码**

新建 `src/mitm_proxy_mcp/addon/__init__.py`（空文件即可）。

新建 `src/mitm_proxy_mcp/addon/traffic_addon.py`：把 `cli/start.py` 中 `addon_script = f'''` 与结尾 `'''` 之间的**全部内容**原样复制过去，然后做且只做这三处机械替换：

1. 所有 `{{` → `{`，所有 `}}` → `}`（还原 f-string 转义）
2. 开头的两行常量改为从环境变量读取：

```python
import os

# Paths come from the environment so this module stays importable — and
# therefore testable — instead of being generated as an f-string at runtime.
#
# 路径改从环境变量读取，这样本模块可以被 import（因而可测），
# 而不是运行时用 f-string 拼出来。
DB_PATH = os.environ.get("MITMPROXY_DB_PATH", "/tmp/mitmproxy-traffic.db")
MOCK_DB_PATH = os.environ.get("MITMPROXY_MOCK_DB_PATH", "/tmp/mitmproxy-mock.db")
```

3. 文件末尾追加模块加载时的初始化（原本靠 f-string 顶层执行）：

```python
init_db()
```

**不要**趁机改动任何逻辑、变量名或 SQL。这一步的唯一目的是让它能被 import。

- [ ] **Step 2: 写重构后的行为测试**

新建 `tests/test_traffic_addon.py`：

```python
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
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/test_traffic_addon.py -v
```

预期：`ModuleNotFoundError: No module named 'mitm_proxy_mcp.addon'`（若尚未创建）或断言失败

- [ ] **Step 4: 让测试通过**

按 Step 1 完成模块抽取，调整到测试全绿。若 `infer_resource_type` 等函数的实际返回值与测试预期不符，**以现有实现为准修改测试**——这是重构，不是改行为。

```bash
uv run pytest tests/test_traffic_addon.py -v
```

预期：全部 passed

- [ ] **Step 5: 让 start.py 改用该模块**

把 `cli/start.py` 中生成 addon 脚本的整段（`addon_script = f'''` 到 `f.write(addon_script)`）替换为：

```python
        # Point the addon at the right databases through the environment, then
        # hand mitmdump the real module path. The addon used to be a 200-line
        # f-string written to /tmp, which could not be imported or tested.
        #
        # 通过环境变量告诉 addon 该用哪个库，再把真实模块路径交给 mitmdump。
        # 这段原本是写到 /tmp 的 200 行 f-string，既不能 import 也无法测试。
        addon_path = str(
            Path(__file__).resolve().parent.parent / "addon" / "traffic_addon.py"
        )
        os.environ["MITMPROXY_DB_PATH"] = str(db_path)
        os.environ["MITMPROXY_MOCK_DB_PATH"] = str(mock_db_path)
```

确认 `start.py` 顶部已 `import os` 和 `from pathlib import Path`（若无则补上）。下方启动 mitmdump 的 `-s addon_path` 参数无需改动。

由于 mitmdump 是子进程，环境变量要显式传入。找到构造 `subprocess.Popen` 的位置，为其补上 `env=os.environ.copy()`（若已使用 `env=` 参数则把两个键并进去）。

- [ ] **Step 6: 端到端验证重构没有改变行为**

```bash
uv run mitmproxy-start --port 8899 &
sleep 6
curl -x http://127.0.0.1:8899 -s http://example.com -o /dev/null
sleep 2
uv run python -c "
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore
store = SQLiteTrafficStore()
records = store.query(limit=5)
assert records, '重构后 addon 没有写入任何流量'
print('抓到', len(records), '条 ·', records[0].method, records[0].url)
"
uv run python -c "
from mitm_proxy_mcp.tools.proxy_tools import proxy_stop
print(proxy_stop(port=8899))
"
```

预期：打印出抓到的记录，证明重构后写库行为不变

- [ ] **Step 7: 跑全量测试并提交**

```bash
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "refactor: 将 mitmproxy addon 从内嵌 f-string 抽成可测试模块"
```

---

### Task 7: TLS 握手失败入库

SSL Pinning 现在的表现是「列表空空如也」——用户完全不知道为什么抓不到。把握手失败的连接也写进 traffic 表，GUI 无需改数据模型就能显示成红色行。

**Files:**
- Modify: `src/mitm_proxy_mcp/addon/traffic_addon.py`
- Test: `tests/test_traffic_addon.py`

**Interfaces:**
- Consumes: mitmproxy 11.0.2 的 `tls_failed_client(data)` 钩子，`data.conn` 带 `error: str | None` 与 `sni: str | None`
- Produces: addon 新增 `tls_failed_client(data)`；写入 traffic 表的记录满足 `status = 0`、`resource_type = "TLS"`、`method = "CONNECT"`、`error` 为握手错误原文、`id` 形如 `tls-<n>`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_traffic_addon.py`：

```python
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
            "SELECT domain, status, resource_type, error FROM traffic"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        domain, status, resource_type, error = rows[0]
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
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_traffic_addon.py::TestTlsFailure -v
```

预期：`AttributeError: module ... has no attribute 'tls_failed_client'`

- [ ] **Step 3: 实现**

在 `src/mitm_proxy_mcp/addon/traffic_addon.py` 末尾（`init_db()` 调用之前）追加：

```python
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
        conn.close()

        print(f"[{counter[0]}] [TLS-FAIL] {sni} -> {error}")

    except Exception as e:
        print(f"Error recording TLS failure: {e}")
```

若 `counter` 在原 addon 中定义在 `response` 附近，确认 `tls_failed_client` 定义在它之后即可复用；若不存在同名变量，在文件常量区加 `counter = [0]`。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_traffic_addon.py -v
```

预期：全部 passed

- [ ] **Step 5: 真实握手失败的端到端验证**

```bash
uv run mitmproxy-start --port 8899 &
sleep 6
# --cacert /dev/null 让客户端拒绝 mitmproxy 的证书，模拟 pinning
curl -x http://127.0.0.1:8899 --cacert /dev/null -s https://example.com -o /dev/null || true
sleep 2
uv run python -c "
import sqlite3
conn = sqlite3.connect('/tmp/mitmproxy-traffic.db')
rows = conn.execute(\"SELECT domain, error FROM traffic WHERE resource_type='TLS'\").fetchall()
conn.close()
assert rows, '未记录到 TLS 握手失败'
print('记录到握手失败:', rows[0])
"
uv run python -c "
from mitm_proxy_mcp.tools.proxy_tools import proxy_stop
print(proxy_stop(port=8899))
"
```

预期：打印出一条 TLS 失败记录

- [ ] **Step 6: 确认 MCP 侧能读到这些记录**

```bash
uv run python -c "
from mitm_proxy_mcp.tools.traffic_tools import traffic_list
result = traffic_list(limit=10, filter_type='TLS')
print(result['returned'], '条 TLS 失败记录')
"
```

预期：至少 1 条

- [ ] **Step 7: 跑全量测试并提交**

```bash
uv run pytest -q && uv run ruff check src tests
git add -A && git commit -m "feat(addon): 记录 TLS 握手失败，让证书绑定可见"
```

---

## 完成标准

全部任务做完后，这些必须成立：

- [ ] `uv run pytest -q` 全绿
- [ ] `uv run ruff check src tests` 无报错
- [ ] `list_tools()` 返回 **32** 个工具（原 26 + 新增 6）
- [ ] 在一台真机或模拟器上，`android_cert_status` 能正确区分证书在用户库还是系统库
- [ ] 用 `--cacert /dev/null` 制造握手失败后，`traffic_list(filter_type="TLS")` 能查到记录

## 后续计划（本计划不含）

| 计划 | 内容 | 依赖 |
|---|---|---|
| Plan 2：控制服务与 MCP 转发层 | GUI 侧本地 HTTP 服务 + `~/.mitmscope/runtime.json` 发现 + token 鉴权；`server.py` 改造成探测到 GUI 就纯转发；MCP shim 自动拉起 GUI | 本计划完成后写，届时工具清单已定型，API 契约才完整 |
| Plan 3：SwiftUI 客户端 | 三栏界面、实时流量推送、Mock 管理、设备与证书面板、MCP 服务面板 | Plan 2 的 API 契约 |
