"""弱网模拟：4G / 3G / 2G 预设，配置文件跨进程共享。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_THROTTLE_PATH = Path("/tmp/mitmproxy-throttle.json")

# 预设对齐常见调试工具量级（延迟 + 上下行带宽）
PROFILES: dict[str, dict[str, int]] = {
    "off": {"latency_ms": 0, "download_kbps": 0, "upload_kbps": 0},
    "4g": {"latency_ms": 20, "download_kbps": 4000, "upload_kbps": 3000},
    "3g": {"latency_ms": 100, "download_kbps": 750, "upload_kbps": 250},
    "2g": {"latency_ms": 300, "download_kbps": 50, "upload_kbps": 20},
}

PROFILE_LABELS = {
    "off": "关闭",
    "4g": "4G",
    "3g": "3G",
    "2g": "2G",
}


@dataclass(frozen=True)
class ThrottleConfig:
    profile: str = "off"
    latency_ms: int = 0
    download_kbps: int = 0
    upload_kbps: int = 0

    @property
    def enabled(self) -> bool:
        return self.profile != "off" and (
            self.latency_ms > 0 or self.download_kbps > 0 or self.upload_kbps > 0
        )

    @property
    def download_bps(self) -> float:
        return self.download_kbps * 1000 / 8 if self.download_kbps > 0 else 0.0

    @property
    def upload_bps(self) -> float:
        return self.upload_kbps * 1000 / 8 if self.upload_kbps > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["enabled"] = self.enabled
        data["label"] = PROFILE_LABELS.get(self.profile, self.profile)
        data["download_bps"] = int(self.download_bps)
        data["upload_bps"] = int(self.upload_bps)
        return data


def normalize_profile(profile: str) -> str:
    key = (profile or "off").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"未知弱网档位: {profile!r}，可选: off / 4g / 3g / 2g")
    return key


def config_for_profile(profile: str) -> ThrottleConfig:
    key = normalize_profile(profile)
    params = PROFILES[key]
    return ThrottleConfig(profile=key, **params)


def get_default_path() -> Path:
    env = os.environ.get("MITMPROXY_THROTTLE_PATH")
    if env:
        return Path(env)
    try:
        from mitm_proxy_mcp.control.paths import throttle_json_path
        from mitm_proxy_mcp.control.runtime import is_pid_alive, read_runtime

        info = read_runtime()
        if info is not None and is_pid_alive(info.pid):
            return throttle_json_path()
    except Exception:
        pass
    return DEFAULT_THROTTLE_PATH


def read_config(path: Path | str | None = None) -> ThrottleConfig:
    target = Path(path) if path is not None else get_default_path()
    if not target.is_file():
        return ThrottleConfig()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ThrottleConfig()
    profile = str(data.get("profile") or "off").lower()
    if profile in PROFILES:
        return config_for_profile(profile)
    return ThrottleConfig(
        profile="custom",
        latency_ms=max(int(data.get("latency_ms") or 0), 0),
        download_kbps=max(int(data.get("download_kbps") or 0), 0),
        upload_kbps=max(int(data.get("upload_kbps") or 0), 0),
    )


def write_config(config: ThrottleConfig, path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else get_default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": config.profile,
        "latency_ms": config.latency_ms,
        "download_kbps": config.download_kbps,
        "upload_kbps": config.upload_kbps,
        "updated_at": time.time(),
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def set_profile(profile: str, path: Path | str | None = None) -> ThrottleConfig:
    config = config_for_profile(profile)
    write_config(config, path)
    return config


def latency_seconds(config: ThrottleConfig) -> float:
    """RTT delay in seconds, 0 when the profile carries no latency.

    RTT 延迟（秒），档位没有配延迟时返回 0。
    """
    return config.latency_ms / 1000.0 if config.latency_ms > 0 else 0.0


def seconds_for_bytes(nbytes: int, bps: float) -> float:
    """Seconds it takes to move `nbytes` at `bps`, 0 when unthrottled.

    以 `bps` 传输 `nbytes` 所需的秒数；不限速时返回 0。
    """
    if bps <= 0 or nbytes <= 0:
        return 0.0
    return nbytes / bps


# Callers on mitmproxy's event loop must await asyncio.sleep() instead: a
# blocking sleep here freezes every other connection, including the Mac's own.
# These sync helpers remain for scripts and tests that run off the loop.
#
# 跑在 mitmproxy 事件循环上的调用方必须改用 await asyncio.sleep()：这里的阻塞
# sleep 会冻住所有其他连接（包括 Mac 自己的）。同步版本仅留给循环外的脚本和测试。
def sleep_latency(config: ThrottleConfig) -> None:
    delay = latency_seconds(config)
    if delay > 0:
        time.sleep(delay)


def sleep_for_bytes(nbytes: int, bps: float) -> None:
    delay = seconds_for_bytes(nbytes, bps)
    if delay > 0:
        time.sleep(delay)


def list_profiles() -> list[dict[str, Any]]:
    items = []
    for key in ("off", "4g", "3g", "2g"):
        cfg = config_for_profile(key)
        items.append(cfg.to_dict())
    return items
