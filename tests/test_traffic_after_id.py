"""traffic_list 的 after_id 增量查询测试。"""

from pathlib import Path

import pytest

from mitm_proxy_mcp.core.models import TrafficRecord
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore
from mitm_proxy_mcp.tools import traffic_tools


@pytest.fixture
def traffic_store(tmp_path: Path) -> SQLiteTrafficStore:
    return SQLiteTrafficStore(tmp_path / "traffic.db")


def _record(record_id: str, timestamp: float) -> TrafficRecord:
    return TrafficRecord(
        id=record_id,
        timestamp=timestamp,
        method="GET",
        url=f"https://example.com/{record_id}",
        domain="example.com",
        status=200,
        resource_type="XHR",
        size=1,
        time_ms=1.0,
    )


def test_after_id_returns_only_strictly_newer_records(
    traffic_store: SQLiteTrafficStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for record_id, timestamp in (("old", 100.0), ("cursor", 200.0), ("new", 300.0)):
        traffic_store.add(_record(record_id, timestamp))
    monkeypatch.setattr(SQLiteTrafficStore, "exists", lambda: True)
    monkeypatch.setattr(traffic_tools, "_get_store", lambda: traffic_store)

    result = traffic_tools.traffic_list(after_id="cursor")

    assert [request["id"] for request in result["requests"]] == ["new"]


def test_after_id_with_same_timestamp_is_excluded(
    traffic_store: SQLiteTrafficStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    traffic_store.add(_record("cursor", 200.0))
    traffic_store.add(_record("same-time", 200.0))
    traffic_store.add(_record("new", 300.0))
    monkeypatch.setattr(SQLiteTrafficStore, "exists", lambda: True)
    monkeypatch.setattr(traffic_tools, "_get_store", lambda: traffic_store)

    result = traffic_tools.traffic_list(after_id="cursor")

    assert [request["id"] for request in result["requests"]] == ["new"]


def test_unknown_after_id_returns_unfiltered_records(
    traffic_store: SQLiteTrafficStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    traffic_store.add(_record("old", 100.0))
    traffic_store.add(_record("new", 300.0))
    monkeypatch.setattr(SQLiteTrafficStore, "exists", lambda: True)
    monkeypatch.setattr(traffic_tools, "_get_store", lambda: traffic_store)

    result = traffic_tools.traffic_list(after_id="missing")

    assert [request["id"] for request in result["requests"]] == ["new", "old"]


def test_traffic_list_hides_connect_tunnels_but_keeps_tls_failures(
    traffic_store: SQLiteTrafficStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    traffic_store.add(_record("get", 100.0))
    traffic_store.add(
        TrafficRecord(
            id="connect",
            timestamp=200.0,
            method="CONNECT",
            url="https://api.example.com:443/",
            domain="api.example.com",
            status=0,
            resource_type="Other",
            size=0,
            time_ms=0.0,
        )
    )
    traffic_store.add(
        TrafficRecord(
            id="tls",
            timestamp=300.0,
            method="CONNECT",
            url="https://pinned.example.com",
            domain="pinned.example.com",
            status=0,
            resource_type="TLS",
            size=0,
            time_ms=0.0,
            error="certificate verify failed",
        )
    )
    monkeypatch.setattr(SQLiteTrafficStore, "exists", lambda: True)
    monkeypatch.setattr(traffic_tools, "_get_store", lambda: traffic_store)

    result = traffic_tools.traffic_list()

    assert [request["id"] for request in result["requests"]] == ["tls", "get"]


@pytest.mark.asyncio
async def test_dispatch_forwards_after_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from mitm_proxy_mcp.control import dispatch

    captured: dict[str, object] = {}

    def fake_traffic_list(**kwargs: object) -> dict[str, bool]:
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "mitm_proxy_mcp.tools.traffic_tools.traffic_list", fake_traffic_list
    )

    await dispatch.invoke_tool("traffic_list", {"after_id": "cursor"})

    assert captured["after_id"] == "cursor"


@pytest.mark.asyncio
async def test_server_schema_exposes_optional_after_id() -> None:
    from mitm_proxy_mcp.server import list_tools

    traffic_list_tool = next(
        tool for tool in await list_tools() if tool.name == "traffic_list"
    )

    assert traffic_list_tool.inputSchema["properties"]["after_id"]["type"] == "string"
