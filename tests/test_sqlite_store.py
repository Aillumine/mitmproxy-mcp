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
