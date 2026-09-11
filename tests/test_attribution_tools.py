"""Tests for filtering traffic by package name and the attribution start/stop tools.

按应用包名过滤流量查询，以及归属采样器的启停控制工具测试。
"""

import pytest

from mitm_proxy_mcp.core.models import TrafficRecord
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore


def _record(rid: str, package: str | None, client_port: int | None = None) -> TrafficRecord:
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
        client_port=client_port,
    )


def test_query_filters_by_package(tmp_path):
    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    store.add(_record("a", "com.example.app", client_port=54321))
    store.add(_record("b", "com.other", client_port=11111))
    store.add(_record("c", None))

    records = store.query(filter_package="com.example.app")

    # The id-set check alone would still pass if _row_to_record() dropped the
    # package/client_port columns on the way back out of SQLite — the filter
    # runs against the raw column, reconstruction is a separate path. Assert
    # the actual field values so that regression can't go silently green.
    #
    # 只检查 id 集合的话，即便 _row_to_record() 在从 SQLite 取回时漏掉了
    # package/client_port 两列，这条测试依然会通过——过滤作用于原始列，
    # 对象重建是另一条独立路径。这里断言具体字段值，回归才不会悄悄放行。
    assert {r.id for r in records} == {"a"}
    assert records[0].package == "com.example.app"
    assert records[0].client_port == 54321


def test_query_without_package_returns_everything(tmp_path):
    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    store.add(_record("a", "com.example.app", client_port=54321))
    store.add(_record("c", None))

    records = store.query()

    assert len(records) == 2
    by_id = {r.id: r for r in records}
    assert by_id["a"].package == "com.example.app"
    assert by_id["a"].client_port == 54321
    assert by_id["c"].package is None


@pytest.mark.asyncio
async def test_start_rejects_an_unknown_package(monkeypatch):
    """An unknown package on the device must fail loudly, not run a sampler that
    can never attribute anything.

    设备上没有这个包时必须明确报错，而不是静默跑一个永远归属不到的采样器。
    """
    from mitm_proxy_mcp.tools import attribution_tools

    async def fake_list_packages(serial):
        return {
            "success": True,
            "packages": [
                {"package": "com.example.app", "uid": 10234, "foreground": False}
            ],
            "count": 1,
        }

    monkeypatch.setattr(attribution_tools, "android_list_packages", fake_list_packages)

    result = await attribution_tools.android_attribute_start("s", "com.nope")

    assert result["success"] is False
    assert "com.nope" in result["message"]


@pytest.mark.asyncio
async def test_start_then_status_then_stop(monkeypatch, tmp_path):
    from mitm_proxy_mcp.tools import attribution_tools

    async def fake_list_packages(serial):
        return {
            "success": True,
            "packages": [
                {"package": "com.example.app", "uid": 10234, "foreground": True}
            ],
            "count": 1,
        }

    class FakeAdb:
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
