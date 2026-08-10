"""Android 代理接入更新抓包目标的测试。"""

from unittest.mock import AsyncMock

import pytest

from mitm_proxy_mcp.control.capture_context import (
    get_capture_target,
    set_capture_target,
)
from mitm_proxy_mcp.tools import android_tools


@pytest.fixture(autouse=True)
def reset_capture_target(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 隔离 HOME，避免用例把真实 ~/.mitmscope/runtime.json 改掉。
    monkeypatch.setenv("HOME", str(tmp_path))
    set_capture_target("mac")
    yield
    set_capture_target("mac")


@pytest.mark.asyncio
async def test_setup_proxy_success_sets_device_capture_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = AsyncMock()
    adb.shell.return_value = (0, "")
    monkeypatch.setattr(android_tools, "_get_adb", lambda: adb)

    result = await android_tools.android_setup_proxy("device-1", "10.0.0.1", 8888)

    assert result["success"] is True
    assert get_capture_target() == "device"


@pytest.mark.asyncio
async def test_reverse_proxy_success_sets_device_capture_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = AsyncMock()
    adb.reverse.return_value = True
    adb.shell_with_exit_code.return_value = (0, "")
    monkeypatch.setattr(android_tools, "_get_adb", lambda: adb)

    result = await android_tools.android_reverse_proxy("device-1", 8888)

    assert result["success"] is True
    assert get_capture_target() == "device"
