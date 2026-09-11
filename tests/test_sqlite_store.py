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


def test_client_port_is_indexed_on_new_and_old_dbs(tmp_path):
    """The backfill UPDATE narrows by client_port once a second; without an
    index that's a full table scan.

    The table caps at 2000 rows and a single body at 1 MiB, and client_port /
    package are the last two columns, so a scan has to walk every row's body
    overflow pages to reach them — potentially hundreds of MB of I/O. An old
    DB adds the column via ALTER TABLE, so the index must land after that
    migration; both paths need covering.

    回填 UPDATE 按 client_port 收窄，每秒一次；没索引就是全表扫描。

    表最大 2000 行、单个 body 上限 1 MiB，而 client_port / package 是最后两列，
    扫描要穿过每行的 body 溢出页才读得到，代价可能是上百 MB 的 I/O。
    老库走 ALTER TABLE 补列，索引必须在补列之后建，所以两条路径都要验。
    """
    import sqlite3

    def index_names(path):
        conn = sqlite3.connect(str(path))
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        conn.close()
        return names

    fresh = tmp_path / "fresh.db"
    SQLiteTrafficStore(db_path=fresh)
    assert "idx_client_port" in index_names(fresh)

    old = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(old))
    conn.execute(
        "CREATE TABLE traffic (id TEXT PRIMARY KEY, timestamp REAL NOT NULL,"
        " method TEXT NOT NULL, url TEXT NOT NULL, domain TEXT NOT NULL,"
        " status INTEGER NOT NULL, resource_type TEXT NOT NULL, size INTEGER NOT NULL,"
        " time_ms REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    SQLiteTrafficStore(db_path=old)
    assert "idx_client_port" in index_names(old)


def test_search_matches_carry_the_package(tmp_path):
    """Search matches must carry package too.

    Selecting an app in the UI filters every row by it; a search result
    missing that column reads as "belongs to some other app" and gets hidden
    wholesale, which looks like search itself is broken.

    搜索结果也要带 package。

    界面上「选中应用」是对所有行生效的过滤，搜索结果缺了这一列就会被整体
    判成「别的应用的」全部隐藏，看起来像搜索坏了。
    """
    store = SQLiteTrafficStore(db_path=tmp_path / "t.db")
    record = _record(1, b"needle in the body")
    record.package = "com.example.app"
    store.add(record)

    by_url = store.search(keyword="e.com/1", search_in=["url"])
    by_body = store.search(keyword="needle", search_in=["response_body"])

    assert [m["package"] for m in by_url] == ["com.example.app"]
    assert [m["package"] for m in by_body] == ["com.example.app"]
