"""
iOS 设备管理模块

支持 iOS 模拟器和真机。
"""

from .ios_client import IOSClient, IOSDeviceInfo, IOSError

__all__ = ["IOSClient", "IOSDeviceInfo", "IOSError"]
