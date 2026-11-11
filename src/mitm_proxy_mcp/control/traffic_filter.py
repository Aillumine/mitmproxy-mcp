"""Durable UI display rules.

持久保存界面的展示过滤规则。
"""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from mitm_proxy_mcp.control.paths import ensure_mitmscope_dir, mitmscope_dir


class TrafficFilter(BaseModel):
    enabled: bool = True
    allow: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)


def read_filter() -> TrafficFilter | None:
    path = mitmscope_dir() / "traffic-filter.json"
    try:
        return TrafficFilter.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None


def write_filter(value: TrafficFilter) -> None:
    # Browser-only rules vanished on origin/port changes. Keep them outside the
    # runtime lifecycle; atomic replacement preserves the last save on failure.
    #
    # 旧规则仅存在浏览器里，地址或端口变化后无法读取。改为独立持久文件，
    # 不随运行时清理；原子替换保证写入失败不会破坏上一次保存。
    directory = ensure_mitmscope_dir()
    fd, name = tempfile.mkstemp(prefix=".traffic-filter-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value.model_dump(), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "traffic-filter.json")
    finally:
        Path(name).unlink(missing_ok=True)
