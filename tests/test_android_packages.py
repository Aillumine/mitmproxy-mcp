import pytest

from mitm_proxy_mcp.android.packages import (
    parse_foreground_package,
    parse_package_uids,
)

PM_OUTPUT = """package:com.example.app uid:10234
package:com.other.thing uid:10666
package:com.broken.line
"""


def test_parses_package_and_uid():
    assert parse_package_uids(PM_OUTPUT) == {
        "com.example.app": 10234,
        "com.other.thing": 10666,
    }


def test_skips_rows_without_uid():
    """少了 uid 的行直接丢掉，别让一行畸形输出弄脏整张表。"""
    assert "com.broken.line" not in parse_package_uids(PM_OUTPUT)


def test_empty_output():
    assert parse_package_uids("") == {}


def test_parses_foreground_package():
    dump = (
        "  mCurrentFocus=Window{a1b2c3 u0 com.example.app/com.example.app.MainActivity}\n"
        "  mFocusedApp=ActivityRecord{d4e5f6 u0 com.example.app/.MainActivity t42}\n"
    )
    assert parse_foreground_package(dump) == "com.example.app"


def test_foreground_missing():
    assert parse_foreground_package("mCurrentFocus=null") is None
    assert parse_foreground_package("") is None


def test_foreground_system_ui_without_slash_is_none():
    """真机实测：锁屏 / 通知栏展开时 mCurrentFocus 是 NotificationShade（无
    包名/Activity 形式），此时必须返回 None，不能被误判成包名。"""
    assert (
        parse_foreground_package(
            "mCurrentFocus=Window{946be20 u0 NotificationShade}"
        )
        is None
    )


def test_foreground_falls_back_to_focused_app_when_current_focus_is_system_ui():
    """真机实测（Pixel 7）：锁屏 / 通知栏展开时 mCurrentFocus 落在系统 UI 上，
    但 mFocusedApp 仍然指向真正的前台应用，必须靠它兜底。"""
    dump = (
        "  mCurrentFocus=Window{946be20 u0 NotificationShade}\n"
        "  mFocusedApp=ActivityRecord{262784378 u0 "
        "com.flow.mobile.debug/com.emochi.app.MainActivity t741}\n"
    )
    assert parse_foreground_package(dump) == "com.flow.mobile.debug"


def test_foreground_prefers_current_focus_over_focused_app():
    """两者都命中且指向不同包名时，mCurrentFocus 更准确，必须优先用它。"""
    dump = (
        "mCurrentFocus=Window{a1 u0 com.current.app/com.current.app.Main}\n"
        "mFocusedApp=ActivityRecord{b2 u0 com.other.app/com.other.app.Main t1}\n"
    )
    assert parse_foreground_package(dump) == "com.current.app"


def test_foreground_none_when_neither_line_matches():
    assert (
        parse_foreground_package(
            "mCurrentFocus=Window{946be20 u0 NotificationShade}\n"
            "mFocusedApp=null\n"
        )
        is None
    )


@pytest.mark.asyncio
async def test_list_packages_puts_the_foreground_app_first(monkeypatch):
    """抓包时想选的几乎总是刚在用的那个，它必须排在第一位。"""
    from mitm_proxy_mcp.tools import android_tools

    calls = []

    async def fake_shell(serial, command, timeout=30.0):
        calls.append(command)
        if "pm list packages" in command:
            return 0, "package:com.example.app uid:10234\npackage:com.zzz uid:10666\n"
        if "dumpsys window" in command:
            return 0, "mCurrentFocus=Window{a1 u0 com.zzz/com.zzz.Main}"
        return 1, ""

    class FakeAdb:
        shell = staticmethod(fake_shell)

    monkeypatch.setattr(android_tools, "_get_adb", lambda: FakeAdb())

    result = await android_tools.android_list_packages("serial123")

    assert result["success"] is True
    assert result["packages"][0]["package"] == "com.zzz", "前台应用必须排最前"
    assert result["packages"][0]["foreground"] is True
    by_name = {p["package"]: p for p in result["packages"]}
    assert by_name["com.example.app"]["uid"] == 10234
    assert by_name["com.example.app"]["foreground"] is False
    assert len(calls) == 2, "只该调两次 adb：一次列包、一次读前台窗口，不做逐包探测"


@pytest.mark.asyncio
async def test_list_packages_reports_a_failed_listing(monkeypatch):
    from mitm_proxy_mcp.tools import android_tools

    async def fake_shell(serial, command, timeout=30.0):
        return 1, "error: device offline"

    class FakeAdb:
        shell = staticmethod(fake_shell)

    monkeypatch.setattr(android_tools, "_get_adb", lambda: FakeAdb())

    result = await android_tools.android_list_packages("serial123")

    assert result["success"] is False
    assert result["packages"] == []
