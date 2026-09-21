"""弱网模拟：4G / 3G / 2G / 断流 预设，配置文件跨进程共享。"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_THROTTLE_PATH = Path("/tmp/mitmproxy-throttle.json")

# 预设对齐常见调试工具量级（延迟 + 上下行带宽）
PROFILES: dict[str, dict[str, int]] = {
    "off": {"latency_ms": 0, "download_kbps": 0, "upload_kbps": 0},
    "4g": {"latency_ms": 20, "download_kbps": 4000, "upload_kbps": 3000},
    "3g": {"latency_ms": 100, "download_kbps": 750, "upload_kbps": 250},
    "2g": {"latency_ms": 300, "download_kbps": 50, "upload_kbps": 20},
    # Not a real network: a deliberately absurd round trip, sized to outlast the
    # longest client receive timeout (120s streaming chat) so timeouts can be
    # provoked on demand. The cellular presets stay physically honest instead of
    # being bent into a stress tool.
    #
    # 这不是真实网络：故意把往返时延拉到超过客户端最长的接收超时（流式聊天 120s），
    # 用来按需压出超时。蜂窝档位因此得以保持物理真实，不用被掰成压测工具。
    "stall": {"latency_ms": 130000, "download_kbps": 1, "upload_kbps": 1},
}

PROFILE_LABELS = {
    "off": "关闭",
    "4g": "4G",
    "3g": "3G",
    "2g": "2G",
    "stall": "断流（压超时）",
}


@dataclass(frozen=True)
class ThrottleConfig:
    profile: str = "off"
    latency_ms: int = 0
    download_kbps: int = 0
    upload_kbps: int = 0
    # Hosts the throttle is allowed to touch; empty means every host. A phone
    # routes far more than the app under test through the proxy, and slowing the
    # OS down has its own consequences — see `host_matches`.
    #
    # 允许被限速的目标域名，空表示不限制。手机经代理的流量远不止被测应用，
    # 拖慢系统本身会有副作用——见 `host_matches`。
    domains: tuple[str, ...] = ()
    # Let Engine.IO ping/pong through untouched; see `is_heartbeat_frame`.
    #
    # 放过 Engine.IO 的 ping/pong 帧；见 `is_heartbeat_frame`。
    heartbeat_exempt: bool = True

    def with_scope(
        self,
        domains: Iterable[str] | None = None,
        heartbeat_exempt: bool | None = None,
    ) -> ThrottleConfig:
        """Copy with the scope settings replaced, hosts normalised to lower case.

        换一份作用范围设置的副本，域名统一转小写。
        """
        changes: dict[str, Any] = {}
        if domains is not None:
            changes["domains"] = normalize_domains(domains)
        if heartbeat_exempt is not None:
            changes["heartbeat_exempt"] = bool(heartbeat_exempt)
        return replace(self, **changes)

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
        data["domains"] = list(self.domains)
        data["enabled"] = self.enabled
        data["label"] = PROFILE_LABELS.get(self.profile, self.profile)
        data["download_bps"] = int(self.download_bps)
        data["upload_bps"] = int(self.upload_bps)
        return data


def normalize_domains(domains: Iterable[str]) -> tuple[str, ...]:
    """Trim, lower-case and drop blanks; DNS names are case-insensitive.

    去空白、转小写、丢掉空项；DNS 域名本来就不区分大小写。
    """
    return tuple(d.strip().lower() for d in domains if d and d.strip())


def host_matches(host: str, patterns: Iterable[str]) -> bool:
    """True when `host` is in scope. No patterns means everything is in scope.

    Patterns are shell globs, so `*.flowgpt.com` covers the subdomains but not
    the bare domain — spell that out as its own entry when you want both.

    `host` 在作用范围内时为 True；没配任何 pattern 就等于全都算在内。
    pattern 是 shell 通配符，`*.flowgpt.com` 只覆盖子域、不含裸域——两个都要
    就再写一条。
    """
    patterns = tuple(patterns)
    if not patterns:
        return True
    host = (host or "").strip().lower()
    if not host:
        return False
    return any(fnmatch.fnmatch(host, pattern) for pattern in patterns)


def normalize_profile(profile: str) -> str:
    key = (profile or "off").strip().lower()
    if key not in PROFILES:
        options = " / ".join(PROFILES)
        raise ValueError(f"未知弱网档位: {profile!r}，可选: {options}")
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
        config = config_for_profile(profile)
        # A stored latency overrides the preset: pressing a 30s or 60s client
        # timeout should not mean waiting out `stall`'s default 130s every run.
        #
        # 文件里的延迟覆盖预设值：压 30s / 60s 的客户端超时，不该每次都等满
        # `stall` 默认的 130s。
        if data.get("latency_ms") is not None:
            config = replace(config, latency_ms=max(int(data["latency_ms"]), 0))
    else:
        config = ThrottleConfig(
            profile="custom",
            latency_ms=max(int(data.get("latency_ms") or 0), 0),
            download_kbps=max(int(data.get("download_kbps") or 0), 0),
            upload_kbps=max(int(data.get("upload_kbps") or 0), 0),
        )
    return config.with_scope(
        domains=data.get("domains") or (),
        heartbeat_exempt=data.get("heartbeat_exempt", True),
    )


def write_config(config: ThrottleConfig, path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else get_default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": config.profile,
        "latency_ms": config.latency_ms,
        "download_kbps": config.download_kbps,
        "upload_kbps": config.upload_kbps,
        "domains": list(config.domains),
        "heartbeat_exempt": config.heartbeat_exempt,
        "updated_at": time.time(),
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def set_profile(
    profile: str,
    path: Path | str | None = None,
    *,
    latency_ms: int | None = None,
    domains: Iterable[str] | None = None,
    heartbeat_exempt: bool | None = None,
) -> ThrottleConfig:
    """Switch profile, carrying the scope settings over unless they are replaced.

    The whitelist is a property of the debugging session, not of the profile —
    flipping 2g → stall must not silently start throttling the phone's OS again.

    切换档位，作用范围设置除非显式覆盖否则原样带过去。
    白名单属于这次调试现场而不是某个档位——2g → stall 不能把手机系统流量
    又悄悄纳入限速。
    """
    current = read_config(path)
    config = config_for_profile(profile).with_scope(
        domains=current.domains if domains is None else domains,
        heartbeat_exempt=(
            current.heartbeat_exempt if heartbeat_exempt is None else heartbeat_exempt
        ),
    )
    if latency_ms is not None:
        config = replace(config, latency_ms=max(int(latency_ms), 0))
    write_config(config, path)
    return config


def latency_seconds(config: ThrottleConfig) -> float:
    """RTT delay in seconds, 0 when the profile carries no latency.

    RTT 延迟（秒），档位没有配延迟时返回 0。
    """
    return config.latency_ms / 1000.0 if config.latency_ms > 0 else 0.0


# Engine.IO v4 packet types: "2" is ping, "3" is pong. Exact match only — the
# shortest business frame socket.io sends is `42[...]`, so nothing else collides.
#
# Engine.IO v4 的包类型："2" 是 ping，"3" 是 pong。只做精确匹配——socket.io
# 最短的业务帧也是 `42[...]`，不会撞上。
_HEARTBEAT_FRAMES = (b"2", b"3")


def is_heartbeat_frame(payload: bytes) -> bool:
    """True for an Engine.IO ping/pong frame.

    Engine.IO 的 ping/pong 帧返回 True。
    """
    return payload in _HEARTBEAT_FRAMES


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
    for key in PROFILES:
        cfg = config_for_profile(key)
        items.append(cfg.to_dict())
    return items
