"""按应用归属流量的启停控制。"""

from pathlib import Path
from typing import Any

from ..android.adb_client import ADBClient
from ..android.attribution import PackageAttributor
from ..core.sqlite_store import SQLiteTrafficStore
from .android_tools import android_list_packages

# One app at a time, mirroring the single selected package in the UI.
#
# 同一时刻只归属一个应用，和界面上「选中一个包名」一一对应。
_attributor: PackageAttributor | None = None


def _get_adb() -> ADBClient:
    return ADBClient()


def _db_path() -> Path:
    return SQLiteTrafficStore.get_default_path()


async def android_attribute_start(serial: str, package: str) -> dict[str, Any]:
    """
    开始把抓到的流量归属到指定应用。

    Args:
        serial: 设备序列号
        package: 目标应用包名（从 android_list_packages 获取）

    Returns:
        {"success": bool, "message": str, "package": str, "uid": int}
    """
    global _attributor

    listing = await android_list_packages(serial)
    if not listing.get("success"):
        return {"success": False, "message": listing.get("message", "读取应用列表失败")}

    target = next(
        (p for p in listing["packages"] if p["package"] == package), None
    )
    if target is None:
        return {"success": False, "message": f"设备上没有找到应用 {package}"}

    if _attributor is not None:
        await _attributor.stop()

    _attributor = PackageAttributor(
        adb=_get_adb(),
        serial=serial,
        package=package,
        uid=target["uid"],
        db_path=_db_path(),
    )
    _attributor.start()
    return {
        "success": True,
        "message": f"已开始归属 {package} 的流量",
        "package": package,
        "uid": target["uid"],
    }


async def android_attribute_stop() -> dict[str, Any]:
    """停止按应用归属流量。"""
    global _attributor
    if _attributor is None:
        return {"success": True, "message": "当前没有在归属流量"}
    package = _attributor.package
    await _attributor.stop()
    _attributor = None
    return {"success": True, "message": f"已停止归属 {package} 的流量"}


async def android_attribute_status() -> dict[str, Any]:
    """查询归属状态：正在归属哪个应用、采样了几轮、归属到多少条、最近一次错误。"""
    if _attributor is None:
        return {"running": False, "package": None}
    return _attributor.status()
