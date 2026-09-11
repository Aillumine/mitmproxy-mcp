"""设备应用清单：包名、uid、当前前台应用。"""

import re

_FOREGROUND_RE = re.compile(r"mCurrentFocus=Window\{[^}]*\s([A-Za-z0-9_.]+)/")


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

    抓包时想选的几乎总是刚在用的那个应用，所以把它排在列表最前。
    """
    match = _FOREGROUND_RE.search(text)
    return match.group(1) if match else None
