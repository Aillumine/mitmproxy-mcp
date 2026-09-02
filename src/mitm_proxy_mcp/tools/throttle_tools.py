"""弱网模拟工具：切换 4G / 3G / 2G / 关闭。"""

from __future__ import annotations

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


def throttle_set(profile: str) -> dict[str, Any]:
    """
    设置弱网档位。

    Args:
        profile: off | 4g | 3g | 2g
    """
    try:
        config = set_profile(profile)
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
    return {
        "success": True,
        "message": message,
        "path": str(get_default_path()),
        **config.to_dict(),
    }
