"""
iOS 工具

提供 iOS 设备管理功能（模拟器和真机）。
"""

from typing import Any

from ..ios.ios_client import IOSClient, IOSError

# 全局 iOS 客户端实例
_ios_client: IOSClient | None = None


def _get_ios_client() -> IOSClient:
    """获取或创建 iOS 客户端实例"""
    global _ios_client
    if _ios_client is None:
        _ios_client = IOSClient()
    return _ios_client


async def ios_list_devices() -> dict[str, Any]:
    """
    列出所有 iOS 设备（模拟器 + 真机）

    Returns:
        包含设备列表的字典
    """
    try:
        client = _get_ios_client()
        devices = await client.list_all()

        device_list = []
        for device in devices:
            device_list.append({
                "udid": device.udid,
                "name": device.name,
                "device_type": device.device_type,
                "state": device.state,
                "os_version": device.os_version,
                "runtime": device.runtime,
                "is_online": device.is_online,
                "is_simulator": device.is_simulator,
                "is_device": device.is_device,
            })

        return {
            "success": True,
            "devices": device_list,
            "count": len(device_list),
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"iOS client error: {e}",
            "devices": [],
            "count": 0,
        }


async def ios_list_simulators() -> dict[str, Any]:
    """
    列出所有 iOS 模拟器

    Returns:
        包含模拟器列表的字典
    """
    try:
        client = _get_ios_client()
        simulators = await client.list_simulators()

        device_list = []
        for sim in simulators:
            device_list.append({
                "udid": sim.udid,
                "name": sim.name,
                "state": sim.state,
                "os_version": sim.os_version,
                "runtime": sim.runtime,
                "is_online": sim.is_online,
            })

        return {
            "success": True,
            "devices": device_list,
            "count": len(device_list),
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"iOS client error: {e}",
            "devices": [],
            "count": 0,
        }


async def ios_list_real_devices() -> dict[str, Any]:
    """
    列出所有 iOS 真机

    Returns:
        包含真机列表的字典
    """
    try:
        client = _get_ios_client()
        devices = await client.list_devices()

        device_list = []
        for device in devices:
            device_list.append({
                "udid": device.udid,
                "name": device.name,
                "state": device.state,
                "is_online": device.is_online,
            })

        return {
            "success": True,
            "devices": device_list,
            "count": len(device_list),
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"iOS client error: {e}",
            "devices": [],
            "count": 0,
        }


async def ios_get_device_info(udid: str) -> dict[str, Any]:
    """
    获取指定 iOS 设备的详细信息

    Args:
        udid: 设备 UDID

    Returns:
        包含设备详细信息的字典
    """
    try:
        client = _get_ios_client()
        device = await client.get_device_info(udid)

        if device is None:
            return {
                "success": False,
                "message": f"Device not found: {udid}",
            }

        return {
            "success": True,
            "device": {
                "udid": device.udid,
                "name": device.name,
                "device_type": device.device_type,
                "state": device.state,
                "os_version": device.os_version,
                "runtime": device.runtime,
                "is_online": device.is_online,
                "is_simulator": device.is_simulator,
                "is_device": device.is_device,
            },
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"iOS client error: {e}",
        }


async def ios_boot_simulator(udid: str) -> dict[str, Any]:
    """
    启动 iOS 模拟器

    Args:
        udid: 模拟器 UDID

    Returns:
        包含启动状态的字典
    """
    try:
        client = _get_ios_client()
        success = await client.boot_simulator(udid)

        return {
            "success": success,
            "message": f"Simulator {udid} booted successfully",
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"Failed to boot simulator: {e}",
        }


async def ios_shutdown_simulator(udid: str) -> dict[str, Any]:
    """
    关闭 iOS 模拟器

    Args:
        udid: 模拟器 UDID

    Returns:
        包含关闭状态的字典
    """
    try:
        client = _get_ios_client()
        success = await client.shutdown_simulator(udid)

        return {
            "success": success,
            "message": f"Simulator {udid} shut down successfully",
        }

    except IOSError as e:
        return {
            "success": False,
            "message": f"Failed to shutdown simulator: {e}",
        }
