"""mitmscope 目录与 DB/runtime/log 路径 helpers。"""

from pathlib import Path

# 目录里存放 runtime.json（含 Bearer token）与抓包数据库，仅本用户可访问。
MITMSCOPE_DIR_MODE = 0o700


def mitmscope_dir() -> Path:
    """返回 ~/.mitmscope 目录路径。"""
    return Path.home() / ".mitmscope"


def ensure_mitmscope_dir() -> Path:
    """确保 ~/.mitmscope 存在、权限为 0700，并返回其路径。"""
    path = mitmscope_dir()
    path.mkdir(parents=True, exist_ok=True, mode=MITMSCOPE_DIR_MODE)
    path.chmod(MITMSCOPE_DIR_MODE)
    return path


def traffic_db_path() -> Path:
    """返回流量 DB 路径（~/.mitmscope/traffic.db）。"""
    return mitmscope_dir() / "traffic.db"


def mock_db_path() -> Path:
    """返回 Mock DB 路径（~/.mitmscope/mock.db）。"""
    return mitmscope_dir() / "mock.db"


def runtime_json_path() -> Path:
    """返回 runtime.json 路径（~/.mitmscope/runtime.json）。"""
    return mitmscope_dir() / "runtime.json"


def control_log_path() -> Path:
    """返回控制服务日志路径（~/.mitmscope/control.log）。"""
    return mitmscope_dir() / "control.log"
