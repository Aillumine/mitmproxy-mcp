"""当前控制服务的抓包目标上下文。"""

from typing import Literal

from loguru import logger

from mitm_proxy_mcp.control.runtime import update_capture_target

CaptureTarget = Literal["device", "simulator", "mac"]

_target: CaptureTarget = "mac"


def get_capture_target() -> CaptureTarget:
    """返回当前抓包目标，默认是本机 Mac。"""
    return _target


def set_capture_target(target: CaptureTarget) -> None:
    """设置当前抓包目标，并尽力同步到 runtime.json。"""
    global _target
    _target = target
    try:
        update_capture_target(target)
    except (OSError, ValueError, TypeError) as error:
        logger.warning(f"同步 capture_target 到 runtime.json 失败: {error}")


def resolve_setup_proxy(requested: bool) -> tuple[bool, str | None]:
    """解析系统代理请求，避免设备抓包时修改 Mac 系统代理。"""
    if _target == "device" and requested:
        return False, "capture_target=device forbids Mac system proxy"
    return requested, None
