"""Device app inventory: package names, uids, and the foreground app.

设备应用清单：包名、uid、当前前台应用。
"""

import re

_CURRENT_FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^}]*\s([A-Za-z0-9_.]+)/")
_FOCUSED_APP_RE = re.compile(r"mFocusedApp=ActivityRecord\{[^}]*\s([A-Za-z0-9_.]+)/")


def parse_package_uids(text: str) -> dict[str, int]:
    """Map package name to uid from `pm list packages -U` output.

    Lines look like `package:com.example.app uid:10234`. Anything that does not
    carry both parts is dropped: a half-parsed row would attribute traffic to the
    wrong app, which is worse than not listing it.

    从 `pm list packages -U` 的输出里解析出「包名 → uid」。

    正常行形如 `package:com.example.app uid:10234`。两部分不齐的行直接丢弃：
    解析了一半的行会把流量归到错误的应用上，比不列出来更糟。
    """
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) < 2:
            continue
        name, uid = fields[0], fields[1]
        if not name.startswith("package:") or not uid.startswith("uid:"):
            continue
        try:
            result[name[len("package:"):]] = int(uid[len("uid:"):])
        except ValueError:
            continue
    return result


def parse_foreground_package(text: str) -> str | None:
    """Package of the focused window from `dumpsys window` output.

    mCurrentFocus is checked first since it names the actually-focused window
    while the device is in normal use. It lands on system UI (NotificationShade)
    whenever the lock screen or notification shade is up — very common when the
    user hands the phone back to the computer — so mFocusedApp, which keeps
    pointing at the real foreground app in that case, is the fallback. The order
    must not be reversed: mFocusedApp can lag behind mCurrentFocus during normal
    app switches.

    抓包时想选的几乎总是刚在用的那个应用，所以把它排在列表最前。

    优先看 mCurrentFocus，因为正常使用时它就是真正聚焦的窗口；但锁屏或下拉
    通知栏时它会落在系统 UI（NotificationShade）上——用户把手机操作完切回
    电脑时经常正是这个状态——这时改用 mFocusedApp 兜底，它仍然指向真正的
    前台应用。顺序不能反：正常切换应用时 mFocusedApp 可能滞后于 mCurrentFocus。
    """
    match = _CURRENT_FOCUS_RE.search(text)
    if match:
        return match.group(1)
    match = _FOCUSED_APP_RE.search(text)
    return match.group(1) if match else None
