# Android 按应用过滤流量 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Traffic 页选择连接设备上的某个应用（包名），列表只显示该应用发出的请求。

**Architecture:** 手机 App 的每条 TCP 连接在 mitmproxy 侧表现为一个「对端端口」（`flow.client_conn.peername[1]`）。设备侧读 `/proc/net/tcp{,6}` 能拿到某个 uid 当前持有的本地端口集合，两边按端口对齐就能把请求精确归属到应用。非 root 设备通过 `run-as <pkg>`（仅 debuggable 应用）取样，root 设备通过 `su` 取样并按 uid 过滤，两条路径共用同一套解析与回填逻辑。

**Tech Stack:** Python 3.11 / mitmproxy addon / SQLite / adb / React 19 + TypeScript + Vite

---

## 调研结论与方案调整（先读这段）

立项时的设定是「阶段一非 root 近似、阶段二 root 精确」。调研后发现**阶段一不必是近似的**，理由如下，方案据此调整：

| 场景 | 能否精确归属 | 手段 |
|---|---|---|
| 自家 debug 包（`android:debuggable=true`） | ✅ 精确 | `adb shell run-as <pkg> cat /proc/net/tcp` —— 非 root 可用 |
| root 设备 / 模拟器上的任意应用 | ✅ 精确 | `su -c cat /proc/net/tcp` + uid 过滤 |
| 非 root 真机上的 release 包 / 第三方应用 | ❌ 做不到 | 降级到域名近似（Task 8） |

Android 10+ 把 `/proc/net/tcp` 按 uid 隔离了：普通进程只能看到自己 uid 的 socket。这条限制通常是障碍，但 `run-as` 恰好把命令切到目标 app 的 uid 执行，于是「只看到自己的连接」反而正是我们要的结果。而用户的主场景就是抓自家 debug 包，所以阶段一直接做到精确，**域名近似降级为兜底（Task 8），不是阶段一的主路径**。

### 已知限制（必须写进 UI 提示）

1. **`adb reverse` 模式下功能不可用。** 仓库里的 `android_reverse_proxy`（`src/mitm_proxy_mcp/tools/android_tools.py:589`）让流量走 adb 转发，源端口会被 adb daemon 改写，端口对齐失效。只有设备直连 Mac 代理（Wi-Fi 填 IP:端口）时能用。
2. **采样有窗口。** 短连接可能在两次采样之间建立又关闭，这类请求归属不到，显示为「未知」而不是错误归属。
3. **非 debuggable + 非 root 直接不可用**，UI 需要明确说明而不是静默失败。

---

## Global Constraints

- Python 侧所有新代码放在 `src/mitm_proxy_mcp/`，测试放 `tests/`，用 `uv run pytest` 跑。
- 前端所有新代码放在 `web/src/`，测试用 `npx vitest run`，改完必须 `npm run build`（产物输出到 `src/mitm_proxy_mcp/webui/`，被 Python 服务直接 serve）。
- 新写或改动的注释一律「英文段 + 空注释行 + 中文段」。
- 新增 MCP 工具必须同时注册三处：`tools/__init__.py` 的导出、`server.py` 的 `Tool(...)` 声明、`control/dispatch.py` 的 `_call_*` 与 `_HANDLERS` 表。漏一处网页端就调不到。
- SQLite 没有迁移框架，加列一律用「`PRAGMA table_info` 查一次 + 缺了就 `ALTER TABLE ADD COLUMN`」，且 **addon 和 sqlite_store 两处建表逻辑都要改**（`addon/traffic_addon.py:64` 与 `core/sqlite_store.py:49`）。
- 端口归属采样默认 1 秒一次，回填窗口 30 秒，两个值定义为模块级常量。

---

### Task 0: 真机验证三条假设（不写生产代码）

整个方案建立在「`run-as` 能读到目标 app 的连接、且端口与 mitmproxy 看到的对端端口一致」之上。先花 10 分钟验证，假设不成立就别往下做。

**Files:**
- Create: `docs/superpowers/plans/2026-09-11-feasibility-notes.md`（记录验证结果）

**Interfaces:**
- Consumes: 无
- Produces: 一份验证结论，决定 Task 4 走 `run-as` 还是直接跳到 Task 7 的 root 路径

- [ ] **Step 1: 确认设备直连代理（不是 adb reverse）**

```bash
adb devices -l
adb shell settings get global http_proxy
```

期望：输出形如 `192.168.x.x:8888`，即设备 Wi-Fi 直连 Mac。如果是 `127.0.0.1:8888`，说明在用 adb reverse，本方案不适用，先改成直连。

- [ ] **Step 2: 验证 run-as 能读到 /proc/net/tcp**

把 `com.your.app` 换成实际 debug 包名，先让 app 产生一些网络请求，再执行：

```bash
adb shell run-as com.your.app cat /proc/net/tcp6 | head -20
```

期望：输出多行，每行第 2 列是 `本地地址:端口` 的十六进制，如 `0000...0000:C1B4`。
失败信号：`run-as: Package 'xxx' is not debuggable` → 该包不可用，只能走 root 路径。

- [ ] **Step 3: 验证端口与 mitmproxy 对端端口一致**

在 mitmproxy 侧临时打印对端端口（改完记得还原）：

```bash
# 临时在 addon 里加一行打印，确认 peername[1] 的值
grep -n "def client_ip" src/mitm_proxy_mcp/addon/traffic_addon.py
```

在 app 里触发一个请求，同时执行 Step 2 的命令，把十六进制端口转成十进制：

```bash
adb shell run-as com.your.app cat /proc/net/tcp6 | awk 'NR>1 {split($2,a,":"); print strtonum("0x" a[2])}' | sort -n
```

期望：mitmproxy 打印的对端端口出现在这个列表里。

- [ ] **Step 4: 记录结论**

把三步的实际输出贴进 `docs/superpowers/plans/2026-09-11-feasibility-notes.md`，写明结论：`run-as 路径可用 / 只能走 root / 端口不一致（方案作废）`。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-09-11-feasibility-notes.md
git commit -m "docs: record feasibility notes for per-package traffic attribution"
```

---

### Task 1: traffic 表新增 client_port 与 package 两列

没有源端口就无从归属。这一步只加字段和落库，不做归属。

**Files:**
- Modify: `src/mitm_proxy_mcp/addon/traffic_addon.py:64`（`init_db`）、`:287`（`client_ip` 附近新增 `client_port`）、`:709` / `:763` / `:824`（三处 INSERT）
- Modify: `src/mitm_proxy_mcp/core/sqlite_store.py:49`（`_init_db`）
- Modify: `src/mitm_proxy_mcp/core/models.py:12`（`TrafficRecord`）
- Test: `tests/test_sqlite_store.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `traffic_addon.client_port(flow) -> int | None`
  - `traffic` 表新增列 `client_port INTEGER`、`package TEXT`
  - `TrafficRecord.client_port: int | None`、`TrafficRecord.package: str | None`
  - `TrafficRecord.to_summary()` 的返回字典新增 `"package"` 键

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sqlite_store.py`：

```python
def test_traffic_table_has_attribution_columns(tmp_path):
    """归属功能依赖 client_port，缺列时整条链路都无从谈起。"""
    from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore

    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    with store._get_conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(traffic)")}
    assert "client_port" in cols
    assert "package" in cols


def test_existing_db_gets_new_columns(tmp_path):
    """老库要能就地升级，不能因为缺列直接炸掉。"""
    import sqlite3

    from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE traffic (id TEXT PRIMARY KEY, timestamp REAL NOT NULL,"
        " method TEXT NOT NULL, url TEXT NOT NULL, domain TEXT NOT NULL,"
        " status INTEGER NOT NULL, resource_type TEXT NOT NULL, size INTEGER NOT NULL,"
        " time_ms REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    SQLiteTrafficStore(db_path=db)
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(traffic)")}
    conn.close()
    assert {"client_port", "package"} <= cols
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_sqlite_store.py -k attribution -v`
Expected: FAIL，`assert "client_port" in cols` 不成立。

- [ ] **Step 3: 实现建表与迁移**

在 `src/mitm_proxy_mcp/core/sqlite_store.py` 的 `_init_db` 里，`CREATE TABLE` 的 `error TEXT,` 之后加两列，并在建完索引后追加迁移调用：

```python
                    error TEXT,
                    client_port INTEGER,
                    package TEXT,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
```

在 `_init_db` 的 `conn.commit()` 之前插入：

```python
            _migrate_attribution_columns(conn)
```

在 `sqlite_store.py` 模块级（`class SQLiteTrafficStore` 之前）加：

```python
# SQLite has no migration framework here, and an existing ~/.mitmscope DB predates
# these columns. ALTER TABLE is the whole migration — adding a nullable column is
# instant and safe to run on every startup.
#
# 这里没有迁移框架，而用户已有的 ~/.mitmscope 数据库建于这两列之前。
# 迁移就是一句 ALTER TABLE——加可空列是瞬时操作，每次启动跑一遍也无妨。
_ATTRIBUTION_COLUMNS = {"client_port": "INTEGER", "package": "TEXT"}


def _migrate_attribution_columns(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(traffic)")}
    for name, sql_type in _ATTRIBUTION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE traffic ADD COLUMN {name} {sql_type}")
```

在 `src/mitm_proxy_mcp/addon/traffic_addon.py` 的 `init_db()` 里，把 `error TEXT` 改成：

```python
            error TEXT,
            client_port INTEGER,
            package TEXT
```

并在 `conn.commit()` 之前加同样的迁移（addon 是独立进程，不能 import 控制服务的模块，所以这段要复制一份）：

```python
    # Same migration as core/sqlite_store.py — the addon runs in its own process
    # and opens the DB first, so it must be able to upgrade an old file too.
    #
    # 与 core/sqlite_store.py 同样的迁移——addon 是独立进程且先打开数据库，
    # 所以它也必须能升级老文件。
    existing = {row[1] for row in conn.execute("PRAGMA table_info(traffic)")}
    for name, sql_type in (("client_port", "INTEGER"), ("package", "TEXT")):
        if name not in existing:
            conn.execute(f"ALTER TABLE traffic ADD COLUMN {name} {sql_type}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_sqlite_store.py -k "attribution or new_columns" -v`
Expected: PASS

- [ ] **Step 5: 写 client_port 的失败测试**

追加到 `tests/test_sqlite_store.py`：

```python
def test_client_port_reads_peer_port():
    """归属靠源端口对齐，取不到时必须是 None 而不是 0。"""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "traffic_addon",
        Path(__file__).resolve().parent.parent
        / "src/mitm_proxy_mcp/addon/traffic_addon.py",
    )
    addon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addon)

    class Conn:
        peername = ("192.168.1.20", 54321)

    class Flow:
        client_conn = Conn()

    assert addon.client_port(Flow()) == 54321

    class NoPeer:
        client_conn = None

    assert addon.client_port(NoPeer()) is None
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_sqlite_store.py -k client_port -v`
Expected: FAIL，`module has no attribute 'client_port'`。

- [ ] **Step 7: 实现 client_port**

在 `src/mitm_proxy_mcp/addon/traffic_addon.py` 的 `client_ip` 函数之后加：

```python
def client_port(flow) -> int | None:
    """Source port of the client connection, None when unavailable.

    This is the join key for per-package attribution: the same number shows up in
    the device's /proc/net/tcp as that app's local port.

    发起这条流的客户端源端口，取不到时返回 None。

    它是按应用归属的关联键：同一个数字会出现在设备 /proc/net/tcp 里，
    作为那个应用的本地端口。
    """
    conn = getattr(flow, "client_conn", None)
    peername = getattr(conn, "peername", None)
    if not peername:
        return None
    try:
        port = peername[1]
    except (TypeError, IndexError):
        return None
    return int(port) if isinstance(port, int) else None
```

- [ ] **Step 8: 三处 INSERT 带上 client_port**

`traffic_addon.py` 共有三处 `INSERT OR REPLACE INTO traffic`（约 `:709`、`:763`、`:824`）。每一处都把列清单里的 `error` 后面加 `, client_port`，占位符多加一个 `?`，参数元组在 `None`（error）之后加 `client_port(flow)`。以第一处为例：

```python
        conn.execute("""
            INSERT OR REPLACE INTO traffic (
                id, timestamp, method, url, domain, status,
                resource_type, size, time_ms, request_headers,
                request_body, request_body_size, response_headers, response_body, error,
                client_port
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_id,
            time.time(),
            flow.request.method,
            url,
            domain,
            flow.response.status_code,
            resource_type,
            size,
            time_ms,
            json.dumps(req_headers),
            req_body,
            req_size,
            json.dumps(res_headers),
            res_body,
            None,
            client_port(flow),
        ))
```

另外两处的列清单不同（一处是错误流、一处是 WebSocket），改法一致：列名末尾加 `client_port`、占位符加一个 `?`、参数末尾加 `client_port(flow)`。

- [ ] **Step 9: 模型带上新字段**

`src/mitm_proxy_mcp/core/models.py`，在 `error: str | None = None` 之后加：

```python
    # 按应用归属用：客户端源端口，以及回填出来的包名
    client_port: int | None = None
    package: str | None = None
```

在 `to_summary()` 的返回字典里，`"error": self.error,` 之后加：

```python
            "package": self.package,
```

- [ ] **Step 10: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 11: Commit**

```bash
git add src/mitm_proxy_mcp/addon/traffic_addon.py src/mitm_proxy_mcp/core/sqlite_store.py src/mitm_proxy_mcp/core/models.py tests/test_sqlite_store.py
git commit -m "feat: record client source port for per-package attribution"
```

---

### Task 2: /proc/net/tcp 解析

纯函数，不碰 adb，可以单独测透。

**Files:**
- Create: `src/mitm_proxy_mcp/android/proc_net.py`
- Test: `tests/test_proc_net.py`

**Interfaces:**
- Consumes: 无
- Produces: `parse_local_ports(text: str, uid: int | None = None) -> set[int]`

- [ ] **Step 1: 写失败测试**

Create `tests/test_proc_net.py`：

```python
from mitm_proxy_mcp.android.proc_net import parse_local_ports

SAMPLE = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10234        0 123456 1 0000000000000000 100 0 0 10 0
   1: 0A00020F:D431 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10234        0 123457 1 0000000000000000 20 4 30 10 -1
   2: 0A00020F:D432 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10666        0 123458 1 0000000000000000 20 4 30 10 -1
"""


def test_parses_every_local_port():
    assert parse_local_ports(SAMPLE) == {8080, 54321, 54322}


def test_filters_by_uid():
    """root 路径下一次能读到全设备的连接，必须按 uid 收窄到目标应用。"""
    assert parse_local_ports(SAMPLE, uid=10234) == {8080, 54321}
    assert parse_local_ports(SAMPLE, uid=10666) == {54322}


def test_ignores_header_and_garbage():
    assert parse_local_ports("") == set()
    assert parse_local_ports("not a table at all") == set()
    assert parse_local_ports("  sl  local_address\n  bad line here\n") == set()


def test_handles_ipv6_rows():
    """tcp6 的地址字段是 32 位十六进制，端口仍在冒号之后。"""
    ipv6 = (
        "  sl  local_address                         remote_address"
        "                        st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
        "   0: 00000000000000000000000000000000:C1B4"
        " 00000000000000000000000000000000:0000 0A 00000000:00000000"
        " 00:00000000 00000000 10234 0 1 1\n"
    )
    assert parse_local_ports(ipv6) == {49588}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_proc_net.py -v`
Expected: FAIL，`ModuleNotFoundError: mitm_proxy_mcp.android.proc_net`。

- [ ] **Step 3: 实现解析**

Create `src/mitm_proxy_mcp/android/proc_net.py`：

```python
"""解析 Android /proc/net/tcp{,6}，取出本地端口集合。"""


def parse_local_ports(text: str, uid: int | None = None) -> set[int]:
    """Local ports held by TCP sockets in a /proc/net/tcp{,6} dump.

    Column 1 is `local_address` as `HEXIP:HEXPORT` (8 hex chars for IPv4, 32 for
    IPv6); column 7 is the owning uid. Malformed lines are skipped rather than
    raised on — this parses whatever a phone happened to print, and one odd row
    must not take down the sampler.

    从 /proc/net/tcp{,6} 的内容里取出所有本地端口。

    第 1 列 local_address 形如 `十六进制IP:十六进制端口`（IPv4 是 8 位、
    IPv6 是 32 位），第 7 列是所属 uid。解析不了的行直接跳过而不是抛异常——
    这是在解析手机随手打印出来的东西，一行异常不能把整个采样器带下去。
    """
    ports: set[int] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        local = fields[1]
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1], 16)
            row_uid = int(fields[7])
        except ValueError:
            continue
        if uid is not None and row_uid != uid:
            continue
        ports.add(port)
    return ports
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_proc_net.py -v`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add src/mitm_proxy_mcp/android/proc_net.py tests/test_proc_net.py
git commit -m "feat: parse local ports from /proc/net/tcp dumps"
```

---

### Task 3: android_list_packages 工具

列出设备上的应用：包名、uid、是否可 `run-as`、是否为当前前台应用。

**注意应用中文名（label）拿不到。** adb 没有直接读 `PackageManager.getApplicationLabel()` 的命令，唯一可靠办法是 pull 出 APK 再用 `aapt2` 解析，几十 MB 的代价换一个名字不划算。本任务只返回包名，并把前台应用排在最前——开发者抓包时想选的几乎总是刚在用的那个。这一点在代码里用 `ponytail:` 注释标记了升级路径。

**Files:**
- Create: `src/mitm_proxy_mcp/android/packages.py`
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`（文件末尾追加）
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`
- Modify: `src/mitm_proxy_mcp/server.py`
- Modify: `src/mitm_proxy_mcp/control/dispatch.py`
- Test: `tests/test_android_packages.py`

**Interfaces:**
- Consumes: `AdbClient.shell(serial, command) -> tuple[int, str]`（`src/mitm_proxy_mcp/android/adb_client.py:159`）
- Produces:
  - `parse_package_uids(text: str) -> dict[str, int]`
  - `parse_foreground_package(text: str) -> str | None`
  - `android_list_packages(serial: str) -> dict[str, Any]`，返回 `{"success": bool, "packages": [{"package": str, "uid": int, "foreground": bool, "debuggable": bool}], "count": int}`

- [ ] **Step 1: 写解析函数的失败测试**

Create `tests/test_android_packages.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_android_packages.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现解析**

Create `src/mitm_proxy_mcp/android/packages.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_android_packages.py -v`
Expected: 5 passed。

- [ ] **Step 5: 写工具层的失败测试**

追加到 `tests/test_android_packages.py`：

```python
import pytest


@pytest.mark.asyncio
async def test_list_packages_marks_foreground_and_debuggable(monkeypatch):
    """前台应用排第一，可 run-as 的应用要标出来——它决定了归属能不能精确。"""
    from mitm_proxy_mcp.tools import android_tools

    async def fake_shell(serial, command, timeout=30.0):
        if "pm list packages" in command:
            return 0, "package:com.example.app uid:10234\npackage:com.zzz uid:10666\n"
        if "dumpsys window" in command:
            return 0, "mCurrentFocus=Window{a1 u0 com.zzz/com.zzz.Main}"
        if "run-as com.example.app" in command:
            return 0, "ok"
        if "run-as com.zzz" in command:
            return 0, "run-as: Package 'com.zzz' is not debuggable"
        return 1, ""

    class FakeAdb:
        shell = staticmethod(fake_shell)

    monkeypatch.setattr(android_tools, "_get_adb", lambda: FakeAdb())

    result = await android_tools.android_list_packages("serial123")

    assert result["success"] is True
    assert result["packages"][0]["package"] == "com.zzz", "前台应用必须排最前"
    assert result["packages"][0]["foreground"] is True
    by_name = {p["package"]: p for p in result["packages"]}
    assert by_name["com.example.app"]["debuggable"] is True
    assert by_name["com.zzz"]["debuggable"] is False
    assert by_name["com.example.app"]["uid"] == 10234
```

异步测试的支持已经就绪：`pyproject.toml` 里 `pytest-asyncio>=0.23.0` 已在依赖中，`asyncio_mode = "auto"` 也配好了（`pyproject.toml:47`），直接写 `async def` 测试即可。

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_android_packages.py -k list_packages -v`
Expected: FAIL，`android_tools has no attribute 'android_list_packages'`。

- [ ] **Step 7: 实现工具**

追加到 `src/mitm_proxy_mcp/tools/android_tools.py` 末尾：

```python
async def android_list_packages(serial: str) -> dict[str, Any]:
    """
    列出设备上已安装的第三方应用，标出当前前台应用与可精确归属的应用。

    Args:
        serial: 设备序列号（从 android_list_devices 获取）

    Returns:
        {"success": bool, "packages": [...], "count": int}
        packages 元素：{"package": 包名, "uid": uid, "foreground": 是否前台,
                       "debuggable": 能否 run-as（决定非 root 下能否精确归属）}
    """
    # ponytail: 只给包名，不给应用中文名。adb 没有读 label 的命令，唯一办法是
    # pull 出 APK 再用 aapt2 解析，几十 MB 换一个名字不值。真需要中文名时，
    # 在这里加「pm path <pkg> → adb pull → aapt2 dump badging」。
    #
    # ponytail: package name only, no display label — adb cannot read it and the
    # only route is pulling the APK for aapt2. Add that here if labels matter.
    try:
        adb = _get_adb()
        code, listing = await adb.shell(serial, "pm list packages -3 -U")
        if code != 0:
            return {
                "success": False,
                "message": f"读取应用列表失败: {listing.strip()}",
                "packages": [],
                "count": 0,
            }

        uids = parse_package_uids(listing)
        _, window_dump = await adb.shell(serial, "dumpsys window | grep mCurrentFocus")
        foreground = parse_foreground_package(window_dump)

        packages = []
        for name, uid in uids.items():
            # run-as 成功与否就是「非 root 下能不能精确归属」的答案，直接探一次。
            #
            # Whether run-as works is exactly the answer to "can we attribute
            # without root", so probe it directly.
            probe_code, probe_out = await adb.shell(
                serial, f"run-as {name} true", timeout=10.0
            )
            debuggable = probe_code == 0 and "not debuggable" not in probe_out
            packages.append({
                "package": name,
                "uid": uid,
                "foreground": name == foreground,
                "debuggable": debuggable,
            })

        packages.sort(key=lambda item: (not item["foreground"], item["package"]))
        return {"success": True, "packages": packages, "count": len(packages)}

    except ADBError as e:
        return {
            "success": False,
            "message": f"ADB error: {e}",
            "packages": [],
            "count": 0,
        }
```

在 `android_tools.py` 的 import 区加：

```python
from ..android.packages import parse_foreground_package, parse_package_uids
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/test_android_packages.py -v`
Expected: 6 passed。

- [ ] **Step 9: 三处注册**

`src/mitm_proxy_mcp/tools/__init__.py`：在 `android_setup_proxy,` 附近的 import 和 `__all__` 里各加一行 `android_list_packages`。

`src/mitm_proxy_mcp/server.py`：在 `Tool(name="android_setup_proxy", ...)` 之前插入：

```python
        Tool(
            name="android_list_packages",
            description="列出 Android 设备上已安装的第三方应用（包名、uid），标出当前前台应用和可精确归属流量的应用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {
                        "type": "string",
                        "description": "设备序列号（从 android_list_devices 获取）",
                    },
                },
                "required": ["serial"],
            },
        ),
```

`src/mitm_proxy_mcp/control/dispatch.py`：在 `_call_android_setup_proxy` 之前加：

```python
async def _call_android_list_packages(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_list_packages",
        arguments["serial"],
    )
```

并在 `_HANDLERS` 字典里加 `"android_list_packages": _call_android_list_packages,`。

- [ ] **Step 10: 验证注册齐全**

```bash
uv run pytest -q
grep -c "android_list_packages" src/mitm_proxy_mcp/tools/__init__.py src/mitm_proxy_mcp/server.py src/mitm_proxy_mcp/control/dispatch.py
```

Expected: 测试全绿；三个文件的计数分别 ≥ 2、≥ 1、≥ 2。

- [ ] **Step 11: Commit**

```bash
git add src/mitm_proxy_mcp/android/packages.py src/mitm_proxy_mcp/tools/ src/mitm_proxy_mcp/server.py src/mitm_proxy_mcp/control/dispatch.py tests/test_android_packages.py
git commit -m "feat: list device packages with uid and attribution capability"
```

---

### Task 4: 端口归属采样器

周期性读目标应用的 `/proc/net/tcp{,6}`，把最近的流量行回填成该包名。

**Files:**
- Create: `src/mitm_proxy_mcp/android/attribution.py`
- Test: `tests/test_attribution.py`

**Interfaces:**
- Consumes:
  - `parse_local_ports(text, uid=None) -> set[int]`（Task 2）
  - `AdbClient.shell(serial, command) -> tuple[int, str]`
  - `traffic` 表的 `client_port` / `package` 列（Task 1）
- Produces:
  - `SAMPLE_INTERVAL_SECONDS = 1.0`、`BACKFILL_WINDOW_SECONDS = 30.0`
  - `sample_command(package: str, uid: int, rooted: bool) -> str`
  - `backfill_packages(conn, package, ports, now, window=BACKFILL_WINDOW_SECONDS) -> int`
  - `class PackageAttributor`，方法 `start()` / `stop()` / `status() -> dict`

- [ ] **Step 1: 写失败测试**

Create `tests/test_attribution.py`：

```python
import sqlite3

from mitm_proxy_mcp.android.attribution import (
    BACKFILL_WINDOW_SECONDS,
    backfill_packages,
    sample_command,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE traffic (id TEXT PRIMARY KEY, timestamp REAL, method TEXT,"
        " url TEXT, domain TEXT, status INTEGER, resource_type TEXT, size INTEGER,"
        " time_ms REAL, error TEXT, client_port INTEGER, package TEXT)"
    )
    return conn


def _row(conn, rid, port, timestamp, package=None):
    conn.execute(
        "INSERT INTO traffic (id, timestamp, method, url, domain, status,"
        " resource_type, size, time_ms, client_port, package)"
        " VALUES (?, ?, 'GET', 'https://x/', 'x', 200, 'XHR', 0, 0, ?, ?)",
        (rid, timestamp, port, package),
    )


def test_non_root_uses_run_as():
    """非 root 靠 run-as 切到应用自己的 uid，才能看到它的连接。"""
    cmd = sample_command("com.example.app", 10234, rooted=False)
    assert "run-as com.example.app" in cmd
    assert "/proc/net/tcp" in cmd


def test_root_uses_su():
    cmd = sample_command("com.example.app", 10234, rooted=True)
    assert cmd.startswith("su -c")
    assert "run-as" not in cmd


def test_backfills_matching_ports():
    conn = _db()
    _row(conn, "a", 54321, 1000.0)
    _row(conn, "b", 54322, 1000.0)

    updated = backfill_packages(conn, "com.example.app", {54321}, now=1001.0)

    assert updated == 1
    rows = dict(conn.execute("SELECT id, package FROM traffic"))
    assert rows["a"] == "com.example.app"
    assert rows["b"] is None


def test_ignores_rows_outside_the_window():
    """端口会被系统复用，超出窗口的老记录不能再认领，否则会张冠李戴。"""
    conn = _db()
    _row(conn, "old", 54321, 1000.0)

    updated = backfill_packages(
        conn, "com.example.app", {54321}, now=1000.0 + BACKFILL_WINDOW_SECONDS + 5
    )

    assert updated == 0
    assert list(conn.execute("SELECT package FROM traffic"))[0][0] is None


def test_never_overwrites_an_existing_package():
    conn = _db()
    _row(conn, "a", 54321, 1000.0, package="com.first.owner")

    updated = backfill_packages(conn, "com.second", {54321}, now=1001.0)

    assert updated == 0
    assert list(conn.execute("SELECT package FROM traffic"))[0][0] == "com.first.owner"


def test_empty_port_set_is_a_noop():
    conn = _db()
    _row(conn, "a", 54321, 1000.0)
    assert backfill_packages(conn, "com.example.app", set(), now=1001.0) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_attribution.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现采样命令与回填**

Create `src/mitm_proxy_mcp/android/attribution.py`：

```python
"""把抓到的请求按源端口归属到设备上的某个应用。"""

import asyncio
import sqlite3
import time
from pathlib import Path

from loguru import logger

from mitm_proxy_mcp.android.proc_net import parse_local_ports

# 采样间隔与回填窗口。窗口不能太长：系统会复用端口，超过窗口的老记录再认领
# 就可能把别的应用的请求算到自己头上。
#
# Sampling interval and backfill window. The window must stay short: the OS
# reuses ports, so claiming rows older than this risks stealing another app's.
SAMPLE_INTERVAL_SECONDS = 1.0
BACKFILL_WINDOW_SECONDS = 30.0


def sample_command(package: str, uid: int, rooted: bool) -> str:
    """Shell command that dumps the target app's TCP sockets.

    Android 10+ shows a process only its own uid's sockets in /proc/net/tcp.
    Without root that restriction is the mechanism, not the obstacle: run-as
    executes as the app's uid, so the dump already contains exactly its
    connections. With root we read everything and filter by uid afterwards.

    返回用于导出目标应用 TCP 连接的 shell 命令。

    Android 10+ 下进程只能在 /proc/net/tcp 里看到自己 uid 的 socket。非 root 时
    这条限制正是实现手段而非障碍：run-as 以应用自己的 uid 执行，导出的内容恰好
    就是它的连接。root 时则一次读全量，之后再按 uid 过滤。
    """
    if rooted:
        return "su -c 'cat /proc/net/tcp /proc/net/tcp6'"
    return f"run-as {package} cat /proc/net/tcp /proc/net/tcp6"


def backfill_packages(
    conn: sqlite3.Connection,
    package: str,
    ports: set[int],
    now: float,
    window: float = BACKFILL_WINDOW_SECONDS,
) -> int:
    """Tag recent rows whose client_port belongs to this app. Returns row count.

    Only rows still unattributed are touched — first writer wins, so a later
    sample of another app can never relabel traffic.

    把最近窗口内、源端口属于该应用的记录打上包名，返回更新行数。

    只改还没归属的行——先到先得，后来的采样不会把已归属的流量改名。
    """
    if not ports:
        return 0
    placeholders = ",".join("?" for _ in ports)
    cursor = conn.execute(
        f"UPDATE traffic SET package = ?"
        f" WHERE package IS NULL AND timestamp >= ?"
        f" AND client_port IN ({placeholders})",
        (package, now - window, *sorted(ports)),
    )
    conn.commit()
    return cursor.rowcount
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_attribution.py -v`
Expected: 6 passed。

- [ ] **Step 5: 写采样循环的失败测试**

追加到 `tests/test_attribution.py`：

```python
import pytest


@pytest.mark.asyncio
async def test_attributor_samples_and_backfills(tmp_path):
    """采样器跑一轮就该把命中端口的请求归属掉。"""
    from mitm_proxy_mcp.android.attribution import PackageAttributor

    db = tmp_path / "traffic.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE traffic (id TEXT PRIMARY KEY, timestamp REAL, method TEXT,"
        " url TEXT, domain TEXT, status INTEGER, resource_type TEXT, size INTEGER,"
        " time_ms REAL, error TEXT, client_port INTEGER, package TEXT)"
    )
    conn.execute(
        "INSERT INTO traffic (id, timestamp, method, url, domain, status,"
        " resource_type, size, time_ms, client_port, package)"
        " VALUES ('a', ?, 'GET', 'https://x/', 'x', 200, 'XHR', 0, 0, 54321, NULL)",
        (time.time(),),
    )
    conn.commit()
    conn.close()

    class FakeAdb:
        @staticmethod
        async def shell(serial, command, timeout=30.0):
            return 0, (
                "  sl  local_address rem_address st tx rx tr tm retr uid\n"
                "   0: 0A00020F:D431 8EFB2D22:01BB 01 0 0 0 0 0 10234 0 1 1\n"
            )

    attributor = PackageAttributor(
        adb=FakeAdb(),
        serial="serial123",
        package="com.example.app",
        uid=10234,
        rooted=False,
        db_path=db,
    )

    updated = await attributor.sample_once()

    assert updated == 1
    conn = sqlite3.connect(str(db))
    assert list(conn.execute("SELECT package FROM traffic"))[0][0] == "com.example.app"
    conn.close()


@pytest.mark.asyncio
async def test_attributor_survives_a_failing_shell(tmp_path):
    """adb 抽风（拔线、应用被杀）时采样器必须继续活着，不能把整个服务带崩。"""
    from mitm_proxy_mcp.android.attribution import PackageAttributor

    db = tmp_path / "traffic.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE traffic (id TEXT PRIMARY KEY, timestamp REAL, method TEXT,"
        " url TEXT, domain TEXT, status INTEGER, resource_type TEXT, size INTEGER,"
        " time_ms REAL, error TEXT, client_port INTEGER, package TEXT)"
    )
    conn.commit()
    conn.close()

    class BrokenAdb:
        @staticmethod
        async def shell(serial, command, timeout=30.0):
            raise OSError("device offline")

    attributor = PackageAttributor(
        adb=BrokenAdb(),
        serial="s",
        package="com.example.app",
        uid=10234,
        rooted=False,
        db_path=db,
    )

    assert await attributor.sample_once() == 0
    assert attributor.status()["last_error"] is not None
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_attribution.py -k attributor -v`
Expected: FAIL，`cannot import name 'PackageAttributor'`。

- [ ] **Step 7: 实现采样器**

追加到 `src/mitm_proxy_mcp/android/attribution.py`：

```python
class PackageAttributor:
    """轮询目标应用的 TCP 连接，把流量记录回填成它的包名。

    Runs as a single asyncio task: one app is being attributed at a time, which
    matches the UI (one selected package). Sampling failures are recorded and
    swallowed — a phone that goes offline must not take the control service with
    it.

    以单个 asyncio 任务运行：同一时刻只归属一个应用，与界面上「选中一个包名」
    一致。采样失败只记录不抛出——手机掉线不能把控制服务一起带走。
    """

    def __init__(
        self,
        adb,
        serial: str,
        package: str,
        uid: int,
        rooted: bool,
        db_path: Path,
    ) -> None:
        self.adb = adb
        self.serial = serial
        self.package = package
        self.uid = uid
        self.rooted = rooted
        self.db_path = Path(db_path)
        self._task: asyncio.Task | None = None
        self._samples = 0
        self._attributed = 0
        self._last_error: str | None = None

    async def sample_once(self) -> int:
        """跑一轮采样并回填，返回本轮归属到的记录数。"""
        command = sample_command(self.package, self.uid, self.rooted)
        try:
            code, output = await self.adb.shell(self.serial, command, timeout=10.0)
        except Exception as error:  # adb 掉线、超时、设备重启都归到这里
            self._last_error = str(error)
            return 0

        if code != 0:
            self._last_error = output.strip()[:200]
            return 0

        ports = parse_local_ports(output, uid=self.uid if self.rooted else None)
        self._samples += 1
        self._last_error = None
        if not ports:
            return 0

        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            updated = backfill_packages(conn, self.package, ports, time.time())
        finally:
            conn.close()
        self._attributed += updated
        return updated

    async def _loop(self) -> None:
        while True:
            await self.sample_once()
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info(f"开始归属 {self.package} 的流量（uid={self.uid}）")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info(f"停止归属 {self.package} 的流量")

    def status(self) -> dict:
        return {
            "package": self.package,
            "uid": self.uid,
            "rooted": self.rooted,
            "running": self._task is not None and not self._task.done(),
            "samples": self._samples,
            "attributed": self._attributed,
            "last_error": self._last_error,
        }
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/test_attribution.py -v`
Expected: 8 passed。

- [ ] **Step 9: Commit**

```bash
git add src/mitm_proxy_mcp/android/attribution.py tests/test_attribution.py
git commit -m "feat: attribute traffic to a package by sampling its TCP sockets"
```

---

### Task 5: 归属的启停工具与按包名查询

把采样器接到 MCP 工具层，并让流量查询能按包名过滤。

**Files:**
- Create: `src/mitm_proxy_mcp/tools/attribution_tools.py`
- Modify: `src/mitm_proxy_mcp/core/sqlite_store.py`（`query` 加参数）
- Modify: `src/mitm_proxy_mcp/tools/traffic_tools.py`（`traffic_list` 加参数）
- Modify: `src/mitm_proxy_mcp/tools/__init__.py`、`src/mitm_proxy_mcp/server.py`、`src/mitm_proxy_mcp/control/dispatch.py`
- Test: `tests/test_attribution_tools.py`

**Interfaces:**
- Consumes: `PackageAttributor`（Task 4）、`android_list_packages`（Task 3）
- Produces:
  - `android_attribute_start(serial: str, package: str) -> dict`
  - `android_attribute_stop() -> dict`
  - `android_attribute_status() -> dict`
  - `SQLiteTrafficStore.query(..., filter_package: str | None = None)`
  - `traffic_list(..., filter_package: str | None = None)`

- [ ] **Step 1: 写查询过滤的失败测试**

Create `tests/test_attribution_tools.py`：

```python
from mitm_proxy_mcp.core.models import TrafficRecord
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore


def _record(rid: str, package: str | None) -> TrafficRecord:
    return TrafficRecord(
        id=rid,
        timestamp=1000.0,
        method="GET",
        url=f"https://example.com/{rid}",
        domain="example.com",
        status=200,
        resource_type="XHR",
        size=0,
        time_ms=1.0,
        package=package,
    )


def test_query_filters_by_package(tmp_path):
    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    store.add(_record("a", "com.example.app"))
    store.add(_record("b", "com.other"))
    store.add(_record("c", None))

    ids = {r.id for r in store.query(filter_package="com.example.app")}

    assert ids == {"a"}


def test_query_without_package_returns_everything(tmp_path):
    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    store.add(_record("a", "com.example.app"))
    store.add(_record("c", None))

    assert len(store.query()) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_attribution_tools.py -v`
Expected: FAIL，`TrafficRecord` 接受了 `package` 但 `query()` 不认 `filter_package`。

- [ ] **Step 3: 实现查询过滤**

`src/mitm_proxy_mcp/core/sqlite_store.py`：`query()` 的签名在 `after_id: str | None = None,` 之前加一行 `filter_package: str | None = None,`；在 `if filter_type:` 那组条件之后加：

```python
        if filter_package:
            conditions.append("package = ?")
            params.append(filter_package)
```

`add()` 的 INSERT 也要带上两列：列清单末尾加 `, client_port, package`，占位符加两个 `?`，参数元组末尾加 `record.client_port, record.package`。同时确认 `query()` 里把行还原成 `TrafficRecord` 的地方带上这两列（搜 `TrafficRecord(` 在本文件中的构造处，补 `client_port=row["client_port"], package=row["package"],`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_attribution_tools.py -v`
Expected: 2 passed。

- [ ] **Step 5: traffic_list 透传参数**

`src/mitm_proxy_mcp/tools/traffic_tools.py`：`traffic_list` 签名里 `after_id` 之前加 `filter_package: str | None = None,`，docstring 补一行 `filter_package: 按应用包名筛选（需先调用 android_attribute_start）`，并在 `store.query(` 的调用里加 `filter_package=filter_package,`。

- [ ] **Step 6: 写启停工具的失败测试**

追加到 `tests/test_attribution_tools.py`：

```python
import pytest


@pytest.mark.asyncio
async def test_start_rejects_a_package_that_cannot_be_attributed(monkeypatch):
    """非 debuggable 又没 root 时必须明确报错，而不是静默跑一个永远归属不到的采样器。"""
    from mitm_proxy_mcp.tools import attribution_tools

    async def fake_list_packages(serial):
        return {
            "success": True,
            "packages": [
                {"package": "com.example.app", "uid": 10234,
                 "foreground": False, "debuggable": False}
            ],
            "count": 1,
        }

    class FakeAdb:
        @staticmethod
        async def is_rooted(serial):
            return False

    monkeypatch.setattr(attribution_tools, "android_list_packages", fake_list_packages)
    monkeypatch.setattr(attribution_tools, "_get_adb", lambda: FakeAdb())

    result = await attribution_tools.android_attribute_start("s", "com.example.app")

    assert result["success"] is False
    assert "debuggable" in result["message"] or "root" in result["message"]


@pytest.mark.asyncio
async def test_start_then_status_then_stop(monkeypatch, tmp_path):
    from mitm_proxy_mcp.tools import attribution_tools

    async def fake_list_packages(serial):
        return {
            "success": True,
            "packages": [
                {"package": "com.example.app", "uid": 10234,
                 "foreground": True, "debuggable": True}
            ],
            "count": 1,
        }

    class FakeAdb:
        @staticmethod
        async def is_rooted(serial):
            return False

        @staticmethod
        async def shell(serial, command, timeout=30.0):
            return 0, ""

    monkeypatch.setattr(attribution_tools, "android_list_packages", fake_list_packages)
    monkeypatch.setattr(attribution_tools, "_get_adb", lambda: FakeAdb())
    monkeypatch.setattr(attribution_tools, "_db_path", lambda: tmp_path / "t.db")

    started = await attribution_tools.android_attribute_start("s", "com.example.app")
    assert started["success"] is True

    status = await attribution_tools.android_attribute_status()
    assert status["package"] == "com.example.app"
    assert status["running"] is True

    stopped = await attribution_tools.android_attribute_stop()
    assert stopped["success"] is True
    assert (await attribution_tools.android_attribute_status())["running"] is False
```

- [ ] **Step 7: 运行测试确认失败**

Run: `uv run pytest tests/test_attribution_tools.py -k "start or stop" -v`
Expected: FAIL，`ModuleNotFoundError: mitm_proxy_mcp.tools.attribution_tools`。

- [ ] **Step 8: 实现启停工具**

Create `src/mitm_proxy_mcp/tools/attribution_tools.py`：

```python
"""按应用归属流量的启停控制。"""

from pathlib import Path
from typing import Any

from ..android.adb_client import AdbClient
from ..android.attribution import PackageAttributor
from ..core.sqlite_store import SQLiteTrafficStore
from .android_tools import android_list_packages

# 同一时刻只归属一个应用，和界面上「选中一个包名」一一对应。
#
# One app at a time, mirroring the single selected package in the UI.
_attributor: PackageAttributor | None = None


def _get_adb() -> AdbClient:
    return AdbClient()


def _db_path() -> Path:
    return SQLiteTrafficStore.get_default_path()


async def android_attribute_start(serial: str, package: str) -> dict[str, Any]:
    """
    开始把抓到的流量归属到指定应用。

    Args:
        serial: 设备序列号
        package: 目标应用包名（从 android_list_packages 获取）

    Returns:
        {"success": bool, "message": str, "package": str, "rooted": bool}
    """
    global _attributor

    listing = await android_list_packages(serial)
    if not listing.get("success"):
        return {"success": False, "message": listing.get("message", "读取应用列表失败")}

    target = next(
        (p for p in listing["packages"] if p["package"] == package), None
    )
    if target is None:
        return {"success": False, "message": f"设备上没有找到应用 {package}"}

    adb = _get_adb()
    rooted = await adb.is_rooted(serial)
    if not rooted and not target["debuggable"]:
        return {
            "success": False,
            "message": (
                f"{package} 不是 debuggable 应用，设备也没有 root，无法精确归属。"
                "请改用 debug 包，或在 root 设备 / 模拟器上抓包。"
            ),
        }

    if _attributor is not None:
        await _attributor.stop()

    _attributor = PackageAttributor(
        adb=adb,
        serial=serial,
        package=package,
        uid=target["uid"],
        rooted=rooted,
        db_path=_db_path(),
    )
    _attributor.start()
    return {
        "success": True,
        "message": f"已开始归属 {package} 的流量",
        "package": package,
        "rooted": rooted,
    }


async def android_attribute_stop() -> dict[str, Any]:
    """停止按应用归属流量。"""
    global _attributor
    if _attributor is None:
        return {"success": True, "message": "当前没有在归属流量"}
    package = _attributor.package
    await _attributor.stop()
    _attributor = None
    return {"success": True, "message": f"已停止归属 {package} 的流量"}


async def android_attribute_status() -> dict[str, Any]:
    """查询归属状态：正在归属哪个应用、采样了几轮、归属到多少条、最近一次错误。"""
    if _attributor is None:
        return {"running": False, "package": None}
    return _attributor.status()
```

- [ ] **Step 9: 运行测试确认通过**

Run: `uv run pytest tests/test_attribution_tools.py -v`
Expected: 4 passed。

- [ ] **Step 10: 三处注册**

`tools/__init__.py` 导出三个新函数并加进 `__all__`。

`server.py` 加三个 `Tool(...)`：

```python
        Tool(
            name="android_attribute_start",
            description="开始把抓到的流量归属到指定应用。仅支持 debuggable 应用或 root 设备。",
            inputSchema={
                "type": "object",
                "properties": {
                    "serial": {"type": "string", "description": "设备序列号"},
                    "package": {"type": "string", "description": "目标应用包名"},
                },
                "required": ["serial", "package"],
            },
        ),
        Tool(
            name="android_attribute_stop",
            description="停止按应用归属流量。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="android_attribute_status",
            description="查询按应用归属的运行状态。",
            inputSchema={"type": "object", "properties": {}},
        ),
```

`control/dispatch.py` 加三个 `_call_*` 并注册进 `_HANDLERS`：

```python
async def _call_android_attribute_start(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.attribution_tools",
        "android_attribute_start",
        serial=arguments["serial"],
        package=arguments["package"],
    )


async def _call_android_attribute_stop(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.attribution_tools", "android_attribute_stop"
    )


async def _call_android_attribute_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.attribution_tools", "android_attribute_status"
    )
```

`traffic_list` 的 `Tool` 声明里，`inputSchema.properties` 补一项：

```python
                    "filter_package": {
                        "type": "string",
                        "description": "按应用包名筛选（需先调用 android_attribute_start）",
                    },
```

并在 `dispatch.py` 里 `traffic_list` 对应的 `_call_*` 中透传 `filter_package=arguments.get("filter_package")`。

- [ ] **Step 11: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全绿。

- [ ] **Step 12: Commit**

```bash
git add src/mitm_proxy_mcp/tools/ src/mitm_proxy_mcp/core/sqlite_store.py src/mitm_proxy_mcp/server.py src/mitm_proxy_mcp/control/dispatch.py tests/test_attribution_tools.py
git commit -m "feat: start/stop package attribution and filter traffic by package"
```

---

### Task 6: Traffic 页的应用选择器

**Files:**
- Create: `web/src/pages/packageFilter.ts`
- Create: `web/src/pages/packageFilter.test.ts`
- Modify: `web/src/pages/Traffic.tsx`
- Modify: `web/src/poll/drainTraffic.ts`（`TrafficRow` 加 `package`）
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `callTool('android_list_packages' | 'android_attribute_start' | 'android_attribute_stop' | 'android_attribute_status')`
- Produces:
  - `TrafficRow.package?: string | null`
  - `PACKAGE_STORAGE_KEY = 'mitm.attributedPackage'`
  - `parsePackages(result) -> DevicePackage[]`
  - `filterRowsByPackage(rows: TrafficRow[], pkg: string | null) -> TrafficRow[]`

- [ ] **Step 1: 写失败测试**

Create `web/src/pages/packageFilter.test.ts`：

```typescript
import { describe, expect, it } from 'vitest';
import type { TrafficRow } from '../poll/drainTraffic';
import { filterRowsByPackage, parsePackages } from './packageFilter';

function row(id: string, pkg: string | null): TrafficRow {
  return {
    id,
    timestamp: 0,
    method: 'GET',
    url: 'https://example.com/',
    domain: 'example.com',
    status: 200,
    type: 'XHR',
    size: 0,
    time: 0,
    error: null,
    package: pkg,
  } as TrafficRow;
}

describe('filterRowsByPackage', () => {
  const rows = [row('a', 'com.example.app'), row('b', 'com.other'), row('c', null)];

  it('returns everything when no package is selected', () => {
    expect(filterRowsByPackage(rows, null)).toHaveLength(3);
  });

  it('keeps only the selected package', () => {
    expect(filterRowsByPackage(rows, 'com.example.app').map((r) => r.id)).toEqual(['a']);
  });

  it('hides rows that have not been attributed yet', () => {
    expect(filterRowsByPackage(rows, 'com.example.app').map((r) => r.id)).not.toContain('c');
  });
});

describe('parsePackages', () => {
  it('reads the tool envelope', () => {
    const parsed = parsePackages({
      packages: [
        { package: 'com.a', uid: 10234, foreground: true, debuggable: true },
        { package: 'com.b', uid: 10666, foreground: false, debuggable: false },
      ],
    });
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toEqual({
      packageName: 'com.a',
      uid: 10234,
      foreground: true,
      debuggable: true,
    });
  });

  it('survives a malformed response', () => {
    expect(parsePackages({})).toEqual([]);
    expect(parsePackages({ packages: 'nope' as unknown as [] })).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/pages/packageFilter.test.ts`
Expected: FAIL，找不到 `./packageFilter`。

- [ ] **Step 3: 实现**

Create `web/src/pages/packageFilter.ts`：

```typescript
import type { TrafficRow } from '../poll/drainTraffic';

export const PACKAGE_STORAGE_KEY = 'mitm.attributedPackage';

export type DevicePackage = {
  packageName: string;
  uid: number;
  foreground: boolean;
  debuggable: boolean;
};

export function parsePackages(result: { packages?: unknown }): DevicePackage[] {
  if (!Array.isArray(result.packages)) return [];
  return result.packages.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const data = item as Record<string, unknown>;
    if (typeof data.package !== 'string') return [];
    return [
      {
        packageName: data.package,
        uid: typeof data.uid === 'number' ? data.uid : 0,
        foreground: data.foreground === true,
        debuggable: data.debuggable === true,
      },
    ];
  });
}

// Rows without a package are hidden while a package is selected: attribution
// runs a second behind the request, so an un-tagged row is either another app's
// or not yet sampled. Showing them would defeat the point of the filter.
//
// 选中应用时，未归属的行一并隐藏：归属比请求慢一拍，没打标的行要么属于别的
// 应用、要么还没采样到。把它们显示出来，这个过滤器就失去意义了。
export function filterRowsByPackage(rows: TrafficRow[], pkg: string | null): TrafficRow[] {
  if (!pkg) return rows;
  return rows.filter((row) => row.package === pkg);
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/pages/packageFilter.test.ts`
Expected: 5 passed。

- [ ] **Step 5: TrafficRow 加字段**

`web/src/poll/drainTraffic.ts` 的 `TrafficRow` 类型里加：

```typescript
  package?: string | null;
```

- [ ] **Step 6: 接进 Traffic 页**

`web/src/pages/Traffic.tsx`：

import 区加：

```typescript
import {
  filterRowsByPackage,
  PACKAGE_STORAGE_KEY,
  parsePackages,
  type DevicePackage,
} from './packageFilter';
```

state 区（`const [patternDraft, setPatternDraft] = useState('');` 附近）加：

```typescript
  const [packages, setPackages] = useState<DevicePackage[]>([]);
  const [activePackage, setActivePackage] = useState<string | null>(() => {
    try {
      return localStorage.getItem(PACKAGE_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [packageBanner, setPackageBanner] = useState<string | null>(null);
```

加载与切换逻辑（放在 `patchDisplayFilter` 附近）：

```typescript
  async function loadPackages(serial: string) {
    const result = await callTool<{ packages?: unknown; message?: string }>(
      'android_list_packages',
      { serial },
    );
    setPackages(parsePackages(result));
  }

  async function selectPackage(serial: string, pkg: string | null) {
    setPackageBanner(null);
    if (!pkg) {
      await callTool('android_attribute_stop', {});
      setActivePackage(null);
      try {
        localStorage.removeItem(PACKAGE_STORAGE_KEY);
      } catch {
        // 私密模式下写不了，不影响本次会话
      }
      return;
    }
    const result = await callTool<{ success?: boolean; message?: string }>(
      'android_attribute_start',
      { serial, package: pkg },
    );
    if (result.success === false) {
      setPackageBanner(result.message ?? '无法归属该应用的流量');
      return;
    }
    setActivePackage(pkg);
    try {
      localStorage.setItem(PACKAGE_STORAGE_KEY, pkg);
    } catch {
      // 同上
    }
  }
```

在计算可见行的地方（搜 `filterTrafficByDisplayRules(` 的调用处）外面再包一层：

```typescript
  const visibleRows = filterRowsByPackage(
    filterTrafficByDisplayRules(kindRows, displayFilter),
    activePackage,
  );
```

在 `<div className="traffic-kinds" ...>` 之后插入选择器：

```tsx
      <div className="package-picker">
        <label htmlFor="package-select">应用</label>
        <select
          id="package-select"
          value={activePackage ?? ''}
          onChange={(event) => {
            const serial = deviceSerial;
            if (!serial) return;
            void selectPackage(serial, event.target.value || null);
          }}
        >
          <option value="">全部应用</option>
          {packages.map((item) => (
            <option
              key={item.packageName}
              value={item.packageName}
              disabled={!item.debuggable}
            >
              {item.packageName}
              {item.foreground ? '（前台）' : ''}
              {item.debuggable ? '' : '（不可归属）'}
            </option>
          ))}
        </select>
        {activePackage ? (
          <span className="muted">只显示 {activePackage} 的请求</span>
        ) : null}
      </div>
      {packageBanner ? <p className="page-banner warn-banner">{packageBanner}</p> : null}
```

`deviceSerial` 从哪来：Traffic 页目前不持有设备序列号。最省的做法是在挂载时拉一次设备列表取第一台：

```typescript
  const [deviceSerial, setDeviceSerial] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await callTool<{ devices?: { serial?: string }[] }>(
        'android_list_devices',
        {},
      );
      const serial = result.devices?.[0]?.serial ?? null;
      if (cancelled || !serial) return;
      setDeviceSerial(serial);
      await loadPackages(serial);
    })();
    return () => {
      cancelled = true;
    };
  }, []);
```

- [ ] **Step 7: 样式**

追加到 `web/src/styles.css`：

```css
.package-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line);
}

.package-picker select {
  max-width: 320px;
  background: var(--bg-1);
  color: inherit;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 4px 6px;
}
```

- [ ] **Step 8: 验证**

```bash
cd web && npx tsc --noEmit -p tsconfig.json && npx vitest run && npm run build
```

Expected: 类型检查无输出、测试全绿、构建成功。

- [ ] **Step 9: 真机走一遍**

1. `uv run mitmproxy-start --port 8888`，手机 Wi-Fi 填 Mac IP:8888
2. 打开网页控制台 → Traffic 页 → 应用下拉选中自家 debug 包
3. 在手机上操作该 app，确认列表只出现它的请求
4. 切回「全部应用」，确认其他应用的请求重新出现

- [ ] **Step 10: Commit**

```bash
git add web/src src/mitm_proxy_mcp/webui
git commit -m "feat(webui): filter traffic by the selected Android package"
```

---

### Task 7: root 设备支持任意应用

Task 4 的 `sample_command` 已经写好了 root 分支，这一步只补 uid 过滤的真实路径和「系统应用也能选」。

**Files:**
- Modify: `src/mitm_proxy_mcp/tools/android_tools.py`（`android_list_packages` 加 `include_system` 参数）
- Modify: `src/mitm_proxy_mcp/server.py`、`src/mitm_proxy_mcp/control/dispatch.py`
- Test: `tests/test_android_packages.py`

**Interfaces:**
- Consumes: `AdbClient.is_rooted(serial) -> bool`（`src/mitm_proxy_mcp/android/adb_client.py:307`）
- Produces: `android_list_packages(serial: str, include_system: bool = False) -> dict`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_android_packages.py`：

```python
@pytest.mark.asyncio
async def test_root_device_can_attribute_any_package(monkeypatch):
    """root 设备上 debuggable 不再是前提，系统应用也应可选。"""
    from mitm_proxy_mcp.tools import android_tools

    commands = []

    async def fake_shell(serial, command, timeout=30.0):
        commands.append(command)
        if "pm list packages" in command:
            return 0, "package:com.android.chrome uid:10111\n"
        if "dumpsys window" in command:
            return 0, ""
        return 1, "run-as: Package 'com.android.chrome' is not debuggable"

    class FakeAdb:
        shell = staticmethod(fake_shell)

        @staticmethod
        async def is_rooted(serial):
            return True

    monkeypatch.setattr(android_tools, "_get_adb", lambda: FakeAdb())

    result = await android_tools.android_list_packages("s", include_system=True)

    assert result["success"] is True
    assert result["packages"][0]["attributable"] is True, "root 下任何应用都能归属"
    assert any("pm list packages -U" in c and "-3" not in c for c in commands)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_android_packages.py -k root_device -v`
Expected: FAIL，`android_list_packages() got an unexpected keyword argument 'include_system'`。

- [ ] **Step 3: 实现**

修改 `android_list_packages`：

```python
async def android_list_packages(
    serial: str, include_system: bool = False
) -> dict[str, Any]:
```

把取列表那行改成：

```python
        listing_command = (
            "pm list packages -U" if include_system else "pm list packages -3 -U"
        )
        code, listing = await adb.shell(serial, listing_command)
```

在 `foreground = parse_foreground_package(window_dump)` 之后加：

```python
        # Root reads every socket table, so run-as is irrelevant there — any app
        # can be attributed. Probing run-as per package is also slow, so skip it.
        #
        # root 能读全量 socket 表，run-as 在那边无关紧要——任何应用都能归属。
        # 逐个探测 run-as 也慢，root 下直接跳过。
        rooted = await adb.is_rooted(serial)
```

把循环内构造 dict 的部分改成：

```python
        for name, uid in uids.items():
            if rooted:
                debuggable = False
            else:
                probe_code, probe_out = await adb.shell(
                    serial, f"run-as {name} true", timeout=10.0
                )
                debuggable = probe_code == 0 and "not debuggable" not in probe_out
            packages.append({
                "package": name,
                "uid": uid,
                "foreground": name == foreground,
                "debuggable": debuggable,
                "attributable": rooted or debuggable,
            })
```

Task 5 的 `android_attribute_start` 里，把判断条件从 `not rooted and not target["debuggable"]` 改成 `not target["attributable"]`。Task 6 前端的 `parsePackages` 加一个 `attributable: data.attributable === true`，`<option disabled>` 的判断从 `!item.debuggable` 改成 `!item.attributable`，文案同步。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_android_packages.py -v && cd web && npx vitest run`
Expected: 两边都全绿。

- [ ] **Step 5: server 与 dispatch 补参数**

`server.py` 的 `android_list_packages` schema 加：

```python
                    "include_system": {
                        "type": "boolean",
                        "description": "是否包含系统应用（仅 root 设备有意义），默认 false",
                        "default": False,
                    },
```

`dispatch.py` 的 `_call_android_list_packages` 改成：

```python
async def _call_android_list_packages(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _call_async(
        "mitm_proxy_mcp.tools.android_tools",
        "android_list_packages",
        serial=arguments["serial"],
        include_system=arguments.get("include_system", False),
    )
```

- [ ] **Step 6: 全量验证**

```bash
uv run pytest -q && cd web && npx tsc --noEmit -p tsconfig.json && npx vitest run && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add src/mitm_proxy_mcp web/src
git commit -m "feat: attribute any package on rooted devices"
```

---

### Task 8: 非 root + 非 debuggable 的域名近似兜底

只在精确归属不可用时出现，且必须让用户知道结果是近似的。

**Files:**
- Modify: `web/src/pages/packageFilter.ts`
- Modify: `web/src/pages/packageFilter.test.ts`
- Modify: `web/src/pages/Traffic.tsx`

**Interfaces:**
- Consumes: `filterRowsByPackage`（Task 6）、`addPattern` / `TrafficDisplayFilter`（`web/src/pages/trafficFilter.ts`）
- Produces: `domainsSeenFor(rows: TrafficRow[], pkg: string) -> string[]`

- [ ] **Step 1: 写失败测试**

追加到 `web/src/pages/packageFilter.test.ts`：

```typescript
import { domainsSeenFor } from './packageFilter';

describe('domainsSeenFor', () => {
  it('collects the domains already attributed to a package', () => {
    const rows = [
      { ...row('a', 'com.example.app'), domain: 'api.example.com' },
      { ...row('b', 'com.example.app'), domain: 'cdn.example.com' },
      { ...row('c', 'com.example.app'), domain: 'api.example.com' },
      { ...row('d', 'com.other'), domain: 'other.com' },
    ] as TrafficRow[];

    expect(domainsSeenFor(rows, 'com.example.app')).toEqual([
      'api.example.com',
      'cdn.example.com',
    ]);
  });

  it('returns nothing for an unknown package', () => {
    expect(domainsSeenFor([], 'com.example.app')).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/pages/packageFilter.test.ts`
Expected: FAIL，`domainsSeenFor is not a function`。

- [ ] **Step 3: 实现**

追加到 `web/src/pages/packageFilter.ts`：

```typescript
// Fallback for phones where exact attribution is impossible: turn the domains a
// package was already seen using into display-filter rules. It is a guess —
// two apps hitting the same CDN are indistinguishable — so the UI must say so.
//
// 精确归属做不到时的兜底：把某个应用已经出现过的域名变成显示过滤规则。
// 这是猜的——两个应用打同一个 CDN 就分不开了——所以界面上必须说清楚。
export function domainsSeenFor(rows: TrafficRow[], pkg: string): string[] {
  const seen = new Set<string>();
  for (const row of rows) {
    if (row.package === pkg && row.domain) seen.add(row.domain);
  }
  return [...seen];
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/pages/packageFilter.test.ts`
Expected: 7 passed。

- [ ] **Step 5: 接进界面**

在 Task 6 的 `.package-picker` 里，`activePackage` 为真时追加一个按钮：

```tsx
        {activePackage ? (
          <button
            type="button"
            className="curl-btn"
            title="把该应用已出现过的域名转成过滤规则，之后即使停止归属也能大致只看它"
            onClick={() => {
              const domains = domainsSeenFor(rows, activePackage);
              if (domains.length === 0) {
                setPackageBanner('还没归属到任何请求，先操作一下这个应用');
                return;
              }
              patchDisplayFilter({
                ...displayFilter,
                enabled: true,
                allow: domains.reduce(
                  (list, domain) => addPattern(list, `*${domain}*`),
                  displayFilter.allow,
                ),
              });
              setPackageBanner(
                `已把 ${domains.length} 个域名加进「只显示」规则，这是近似结果：` +
                  '共用同一个 CDN 的其他应用也会被显示。',
              );
            }}
          >
            固化为域名规则
          </button>
        ) : null}
```

import 区补 `domainsSeenFor` 和 `addPattern`（`addPattern` 来自 `./trafficFilter`，Task 6 之后 Traffic.tsx 里可能已经没有它的 import 了，需要补回）。

- [ ] **Step 6: 验证**

```bash
cd web && npx tsc --noEmit -p tsconfig.json && npx vitest run && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add web/src src/mitm_proxy_mcp/webui
git commit -m "feat(webui): freeze attributed domains into display rules as a fallback"
```

---

## 自查记录

**需求覆盖：**
- 「获取设备所有应用名称和包名」→ Task 3（包名 + uid + 前台标记；应用中文名拿不到，已在 Task 3 说明原因并留升级路径）
- 「选择包名把该应用所有请求获取到」→ Task 1 + 2 + 4 + 5（源端口对齐 + 采样回填 + 按包名查询）
- 「其他应用不展示」→ Task 6（前端过滤 + 未归属行一并隐藏）
- 「分阶段：先近似后精确」→ 调整为：Task 1-6 精确（debuggable 应用）、Task 7 精确（root 任意应用）、Task 8 近似兜底，理由见开头「调研结论与方案调整」

**类型一致性：** `android_list_packages` 在 Task 3 返回 `debuggable`，Task 7 增加 `attributable` 并把下游判断切过去，Task 5 的 `android_attribute_start` 与 Task 6 的前端已同步说明改动点。`TrafficRecord.package` / `TrafficRow.package` / SQL 列 `package` 三处命名一致。

**未决问题（执行前请确认）：** Task 0 的三条假设若不成立——尤其是「mitmproxy 看到的对端端口与设备 `/proc/net/tcp` 的本地端口一致」——Task 1-7 全部作废，只剩 Task 8 的近似方案可用。所以 Task 0 必须先做。
