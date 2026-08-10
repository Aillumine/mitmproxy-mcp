"""当前控制服务的抓包目标上下文。"""

from typing import Literal

CaptureTarget = Literal["device", "simulator", "mac"]

_target: CaptureTarget = "mac"


def get_capture_target() -> CaptureTarget:
    """返回当前抓包目标，默认是本机 Mac。"""
    return _target


def set_capture_target(target: CaptureTarget) -> None:
    """设置当前抓包目标。"""
    global _target
    _target = target


def resolve_setup_proxy(requested: bool) -> tuple[bool, str | None]:
    """解析系统代理请求，避免设备抓包时修改 Mac 系统代理。"""
    if _target == "device" and requested:
        return False, "capture_target=device forbids Mac system proxy"
    return requested, None
