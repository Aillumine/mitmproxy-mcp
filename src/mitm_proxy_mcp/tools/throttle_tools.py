"""弱网模拟工具：切换 4G / 3G / 2G / 断流 / 关闭。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..core.throttle import (
    PROFILE_LABELS,
    get_default_path,
    list_profiles,
    read_config,
    set_profile,
)


def throttle_get() -> dict[str, Any]:
    """获取当前弱网档位与参数。"""
    config = read_config()
    return {
        "success": True,
        "path": str(get_default_path()),
        "profiles": list_profiles(),
        **config.to_dict(),
    }


def throttle_set(
    profile: str,
    domains: Iterable[str] | None = None,
    keep_connection_alive: bool | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    """
    设置弱网档位。

    Args:
        profile: off | 4g | 3g | 2g | stall
        domains: 只限这些目标域名，支持通配（如 *.flowgpt.com）；传 [] 表示限全部。
                 不传则沿用现有设置。
        keep_connection_alive: 是否放过维持 WebSocket 存活的流量——upgrade 握手和
                 Engine.IO 心跳帧（默认放过）。关掉则连接会被压死。
        latency_ms: 覆盖该档位的 RTT，用来按目标超时挑值（如压 30s 超时用 35000）。
    """
    try:
        config = set_profile(
            profile,
            latency_ms=latency_ms,
            domains=domains,
            keep_connection_alive=keep_connection_alive,
        )
    except ValueError as exc:
        return {
            "success": False,
            "message": str(exc),
            "profiles": list(PROFILE_LABELS.keys()),
        }
    label = PROFILE_LABELS.get(config.profile, config.profile)
    if config.profile == "off":
        message = "已关闭弱网模拟，流量按原速转发。"
    else:
        message = (
            f"已切换弱网：{label}（延迟 {config.latency_ms}ms，"
            f"下行 {config.download_kbps} kbps，上行 {config.upload_kbps} kbps）。"
            "代理运行中立即生效，无需重启。"
        )
    scope = "、".join(config.domains) if config.domains else "全部流量（未配白名单）"
    message += f" 作用范围：{scope}。"
    if not config.keep_connection_alive:
        message += " WebSocket 握手与心跳帧一并限速（会压出断连而不是超时）。"
    return {
        "success": True,
        "message": message,
        "path": str(get_default_path()),
        **config.to_dict(),
    }
