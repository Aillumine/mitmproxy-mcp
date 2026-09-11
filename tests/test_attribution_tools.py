"""按应用包名过滤流量查询，以及归属采样器的启停控制工具测试。"""

import pytest

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


@pytest.mark.asyncio
async def test_start_rejects_an_unknown_package(monkeypatch):
    """设备上没有这个包时必须明确报错，而不是静默跑一个永远归属不到的采样器。"""
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
