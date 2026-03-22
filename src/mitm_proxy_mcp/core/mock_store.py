"""
Mock 规则存储

基于 SQLite 的 mock 规则存储，与代理共享数据库文件。
代理在 request 阶段读取匹配的规则并返回 mock 响应。
"""

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

DEFAULT_MOCK_DB_PATH = Path("/tmp/mitmproxy-mock.db")


@dataclass
class MockRule:
    """Mock 规则"""

    id: str
    name: str
    url_pattern: str  # URL 匹配模式，支持通配符 * 和正则
    method: str = ""  # HTTP 方法，为空则匹配所有方法
    status_code: int = 200
    response_headers: dict[str, str] = field(default_factory=lambda: {"content-type": "application/json"})
    response_body: str = ""
    delay_ms: int = 0  # 模拟延迟（毫秒）
    enabled: bool = True
    match_type: str = "contains"  # contains | exact | regex
    created_at: float = 0.0
    updated_at: float = 0.0
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url_pattern": self.url_pattern,
            "method": self.method or "*",
            "match_type": self.match_type,
            "status_code": self.status_code,
            "response_headers": self.response_headers,
            "response_body_preview": self.response_body[:200] + ("..." if len(self.response_body) > 200 else ""),
            "response_body_size": len(self.response_body),
            "delay_ms": self.delay_ms,
            "enabled": self.enabled,
            "hit_count": self.hit_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MockStore:
    """Mock 规则 SQLite 存储"""

    def __init__(self, db_path: Path | str = DEFAULT_MOCK_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mock_rules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url_pattern TEXT NOT NULL,
                    method TEXT DEFAULT '',
                    status_code INTEGER DEFAULT 200,
                    response_headers TEXT DEFAULT '{}',
                    response_body TEXT DEFAULT '',
                    delay_ms INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1,
                    match_type TEXT DEFAULT 'contains',
                    created_at REAL,
                    updated_at REAL,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def add(self, rule: MockRule) -> None:
        now = time.time()
        if not rule.created_at:
            rule.created_at = now
        rule.updated_at = now

        with self._lock:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO mock_rules (
                        id, name, url_pattern, method, status_code,
                        response_headers, response_body, delay_ms,
                        enabled, match_type, created_at, updated_at, hit_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule.id, rule.name, rule.url_pattern, rule.method,
                    rule.status_code, json.dumps(rule.response_headers),
                    rule.response_body, rule.delay_ms,
                    1 if rule.enabled else 0, rule.match_type,
                    rule.created_at, rule.updated_at, rule.hit_count,
                ))
                conn.commit()

    def get_by_id(self, rule_id: str) -> MockRule | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM mock_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            return self._row_to_rule(row) if row else None

    def list_all(self, enabled_only: bool = False) -> list[MockRule]:
        with self._get_conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM mock_rules WHERE enabled = 1 ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mock_rules ORDER BY updated_at DESC"
                ).fetchall()
            return [self._row_to_rule(row) for row in rows]

    def update(self, rule_id: str, **kwargs: Any) -> bool:
        rule = self.get_by_id(rule_id)
        if not rule:
            return False

        kwargs["updated_at"] = time.time()

        set_clauses = []
        params = []
        for key, value in kwargs.items():
            if key == "response_headers" and isinstance(value, dict):
                value = json.dumps(value)
            if key == "enabled":
                value = 1 if value else 0
            set_clauses.append(f"{key} = ?")
            params.append(value)

        params.append(rule_id)

        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    f"UPDATE mock_rules SET {', '.join(set_clauses)} WHERE id = ?",
                    params,
                )
                conn.commit()
        return True

    def delete(self, rule_id: str) -> bool:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM mock_rules WHERE id = ?", (rule_id,)
                )
                conn.commit()
                return cursor.rowcount > 0

    def clear(self) -> int:
        with self._lock:
            with self._get_conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM mock_rules").fetchone()[0]
                conn.execute("DELETE FROM mock_rules")
                conn.commit()
                return count

    def increment_hit(self, rule_id: str) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE mock_rules SET hit_count = hit_count + 1 WHERE id = ?",
                    (rule_id,),
                )
                conn.commit()

    def __len__(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM mock_rules").fetchone()[0]

    def _row_to_rule(self, row: sqlite3.Row) -> MockRule:
        return MockRule(
            id=row["id"],
            name=row["name"],
            url_pattern=row["url_pattern"],
            method=row["method"] or "",
            status_code=row["status_code"],
            response_headers=json.loads(row["response_headers"] or "{}"),
            response_body=row["response_body"] or "",
            delay_ms=row["delay_ms"] or 0,
            enabled=bool(row["enabled"]),
            match_type=row["match_type"] or "contains",
            created_at=row["created_at"] or 0.0,
            updated_at=row["updated_at"] or 0.0,
            hit_count=row["hit_count"] or 0,
        )

    @classmethod
    def get_default_path(cls) -> Path:
        return DEFAULT_MOCK_DB_PATH

    @staticmethod
    def match_url(url: str, pattern: str, match_type: str) -> bool:
        """检查 URL 是否匹配模式"""
        if match_type == "exact":
            return url == pattern
        elif match_type == "regex":
            try:
                return bool(re.search(pattern, url))
            except re.error:
                return False
        else:  # contains
            if "*" in pattern:
                regex_pattern = re.escape(pattern).replace(r"\*", ".*")
                try:
                    return bool(re.search(regex_pattern, url))
                except re.error:
                    return False
            return pattern in url
