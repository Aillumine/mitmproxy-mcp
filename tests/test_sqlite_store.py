"""SQLiteTrafficStore 的存储与空间回收行为。"""

from pathlib import Path

from mitm_proxy_mcp.core.models import TrafficRecord
from mitm_proxy_mcp.core.sqlite_store import SQLiteTrafficStore


def _record(idx: int, body: bytes) -> TrafficRecord:
    return TrafficRecord(
        id=f"req-{idx}",
        timestamp=float(idx),
        method="GET",
        url=f"https://e.com/{idx}",
        domain="e.com",
        status=200,
        resource_type="XHR",
        size=len(body),
        time_ms=1.0,
        request_headers={},
        response_headers={},
        response_body=body,
    )


def test_clear_reclaims_disk_space(tmp_path):
    """
    DELETE alone leaves the pages allocated, so a database that once held large
    bodies never shrinks — one real db sat at 527 MB while holding 123 rows.

    只 DELETE 会把页留在原地，存过大 body 的库永远不会变小——
    线上曾出现只有 123 行却占 527MB 的库。
    """
    db = tmp_path / "traffic.db"
    store = SQLiteTrafficStore(db)
    for i in range(200):
        store.add(_record(i, b"x" * 50_000))

    grown = db.stat().st_size
    assert grown > 5_000_000

    store.clear()

    assert len(store) == 0
    assert db.stat().st_size < grown // 2


def test_clear_on_empty_store_is_a_noop(tmp_path):
    db: Path = tmp_path / "traffic.db"
    store = SQLiteTrafficStore(db)
    store.clear()
    assert len(store) == 0
