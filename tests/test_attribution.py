"""Tests for the port-attribution sampler: the shape of the sample command, and
the two rules that keep backfill from mis-attributing traffic — the backfill
window and touching only still-unowned rows.

端口归属采样器的测试：采样命令的形态，以及回填窗口 / 只改未归属行两条关键规则。
"""

import sqlite3
import time

import pytest

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


def test_samples_both_socket_tables():
    """IPv4 连接在 /proc/net/tcp、应用连接多在 tcp6，只读一张会漏。"""
    cmd = sample_command()
    assert "/proc/net/tcp " in cmd or cmd.endswith("/proc/net/tcp")
    assert "/proc/net/tcp6" in cmd


def test_needs_neither_root_nor_run_as():
    """adb shell 自己就在 readproc 组里，能读全量表——实测 run-as 反而被 SELinux 拒绝。"""
    cmd = sample_command()
    assert "su" not in cmd.split()
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
            # tx_queue:rx_queue and tr:tm->when must stay colon-joined single
            # fields (matching the verified real format in test_proc_net.py),
            # or the uid column shifts out of place.
            #
            # tx_queue:rx_queue 与 tr:tm->when 必须是冒号拼接的单个字段（与
            # test_proc_net.py 里已验证过的真实格式一致），否则 uid 会错位。
            return 0, (
                "  sl  local_address rem_address st tx rx tr tm retr uid\n"
                "   0: 0A00020F:D431 8EFB2D22:01BB 01 00000000:00000000"
                " 00:00000000 00000000 10234 0 1 1\n"
            )

    attributor = PackageAttributor(
        adb=FakeAdb(),
        serial="serial123",
        package="com.example.app",
        uid=10234,
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
        db_path=db,
    )

    assert await attributor.sample_once() == 0
    assert attributor.status()["last_error"] is not None


@pytest.mark.asyncio
async def test_attributor_survives_a_broken_database(tmp_path):
    """DB path unavailable (locked, missing directory, stale schema) must not
    escape sample_once and kill the polling loop either — same contract as an
    adb failure.

    数据库打不开（被锁、目录不存在、schema 过期）同样不能从 sample_once 逃出去、
    把轮询循环带崩——和 adb 失败走同一份「记录后继续」的承诺。
    """
    from mitm_proxy_mcp.android.attribution import PackageAttributor

    # Points at a directory that was never created: sqlite3.connect raises
    # OperationalError.
    #
    # 指向一个不存在的目录：sqlite3.connect 会抛 OperationalError。
    db = tmp_path / "no-such-dir" / "traffic.db"

    class FakeAdb:
        @staticmethod
        async def shell(serial, command, timeout=30.0):
            return 0, (
                "  sl  local_address rem_address st tx rx tr tm retr uid\n"
                "   0: 0A00020F:D431 8EFB2D22:01BB 01 00000000:00000000"
                " 00:00000000 00000000 10234 0 1 1\n"
            )

    attributor = PackageAttributor(
        adb=FakeAdb(),
        serial="serial123",
        package="com.example.app",
        uid=10234,
        db_path=db,
    )

    assert await attributor.sample_once() == 0
    assert attributor.status()["last_error"] is not None
