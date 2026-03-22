"""
iOS 客户端

支持 iOS 模拟器（通过 xcrun simctl）和真机（通过 libimobiledevice）。
"""

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class IOSDeviceInfo:
    """iOS 设备信息"""

    udid: str  # 设备唯一标识
    name: str
    device_type: Literal["simulator", "device"]  # 模拟器或真机
    state: str  # "Booted" | "Shutdown" | "Shutting Down" | etc.
    os_version: str | None = None
    runtime: str | None = None  # iOS 版本，如 "iOS 17.0"
    extra_info: dict[str, str] = field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        """设备是否在线可用"""
        return self.state == "Booted"

    @property
    def is_simulator(self) -> bool:
        """是否为模拟器"""
        return self.device_type == "simulator"

    @property
    def is_device(self) -> bool:
        """是否为真机"""
        return self.device_type == "device"


class IOSError(Exception):
    """iOS 操作错误"""

    def __init__(self, message: str, returncode: int = -1, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class IOSClient:
    """
    iOS 客户端

    封装 iOS 设备管理命令，支持模拟器和真机。
    """

    def __init__(self):
        """初始化 iOS 客户端"""
        self._simctl_path = shutil.which("xcrun")
        self._idevice_id_path = shutil.which("idevice_id")

    async def _run_command(
        self,
        *args: str,
        timeout: float = 30.0,
    ) -> tuple[int, str, str]:
        """
        执行命令

        Args:
            *args: 命令参数
            timeout: 超时时间（秒）

        Returns:
            (returncode, stdout, stderr)
        """
        cmd = list(args)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            return (
                proc.returncode or 0,
                stdout.decode("utf-8", errors="replace").strip(),
                stderr.decode("utf-8", errors="replace").strip(),
            )
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
                await proc.wait()
            raise IOSError(f"Command timed out: {' '.join(cmd)}")
        except Exception as e:
            raise IOSError(f"Command failed: {e}")

    async def list_simulators(self) -> list[IOSDeviceInfo]:
        """
        列出所有 iOS 模拟器

        Returns:
            模拟器列表
        """
        if not self._simctl_path:
            raise IOSError("xcrun not found. Install Xcode Command Line Tools.")

        # 使用 xcrun simctl list devices --json
        returncode, stdout, stderr = await self._run_command(
            self._simctl_path,
            "simctl",
            "list",
            "devices",
            "--json",
        )

        if returncode != 0:
            raise IOSError(f"Failed to list simulators: {stderr}", returncode, stderr)

        try:
            data = json.loads(stdout)
            devices = []

            # 解析 JSON 结构
            # {"devices": {"iOS 17.0": [{"name": "...", "udid": "...", "state": "..."}]}}
            for runtime, device_list in data.get("devices", {}).items():
                if not isinstance(device_list, list):
                    continue

                for device_data in device_list:
                    if not isinstance(device_data, dict):
                        continue

                    udid = device_data.get("udid", "")
                    name = device_data.get("name", "")
                    state = device_data.get("state", "Unknown")

                    # 只包含模拟器（排除 watchOS, tvOS 等）
                    if "iPhone" in name or "iPad" in name or "iPod" in name:
                        devices.append(
                            IOSDeviceInfo(
                                udid=udid,
                                name=name,
                                device_type="simulator",
                                state=state,
                                runtime=runtime,
                                os_version=self._extract_os_version(runtime),
                            )
                        )

            return devices
        except json.JSONDecodeError as e:
            raise IOSError(f"Failed to parse simulator list: {e}")

    async def list_devices(self) -> list[IOSDeviceInfo]:
        """
        列出所有 iOS 真机

        Returns:
            真机列表
        """
        if not self._idevice_id_path:
            # 如果没有 libimobiledevice，返回空列表而不是报错
            return []

        returncode, stdout, stderr = await self._run_command(
            self._idevice_id_path,
            "-l",
        )

        if returncode != 0:
            # 如果没有设备连接，返回空列表
            return []

        udids = [line.strip() for line in stdout.splitlines() if line.strip()]
        devices = []

        for udid in udids:
            # 获取设备名称（如果可能）
            name = await self._get_device_name(udid)
            devices.append(
                IOSDeviceInfo(
                    udid=udid,
                    name=name or udid,
                    device_type="device",
                    state="Connected",  # 如果能列出，说明已连接
                    os_version=None,  # 需要额外命令获取
                )
            )

        return devices

    async def list_all(self) -> list[IOSDeviceInfo]:
        """
        列出所有 iOS 设备（模拟器 + 真机）

        Returns:
            所有设备列表
        """
        simulators = await self.list_simulators()
        devices = await self.list_devices()
        return simulators + devices

    async def _get_device_name(self, udid: str) -> str | None:
        """获取设备名称（真机）"""
        ideviceinfo_path = shutil.which("ideviceinfo")
        if not ideviceinfo_path:
            return None

        try:
            returncode, stdout, _ = await self._run_command(
                ideviceinfo_path,
                "-u",
                udid,
                "-k",
                "DeviceName",
                timeout=5.0,
            )
            if returncode == 0 and stdout:
                return stdout.strip()
        except Exception:
            pass

        return None

    def _extract_os_version(self, runtime: str) -> str | None:
        """从 runtime 字符串中提取 iOS 版本"""
        # runtime 格式如 "iOS 17.0" 或 "com.apple.CoreSimulator.SimRuntime.iOS-17-0"
        if "iOS" in runtime:
            # 尝试提取版本号
            parts = runtime.split()
            for part in parts:
                if part.replace(".", "").isdigit():
                    return part
        return None

    async def get_device_info(self, udid: str) -> IOSDeviceInfo | None:
        """
        获取指定设备的详细信息

        Args:
            udid: 设备 UDID

        Returns:
            设备信息，如果未找到返回 None
        """
        all_devices = await self.list_all()
        return next((d for d in all_devices if d.udid == udid), None)

    async def boot_simulator(self, udid: str) -> bool:
        """
        启动模拟器

        Args:
            udid: 模拟器 UDID

        Returns:
            是否成功
        """
        if not self._simctl_path:
            raise IOSError("xcrun not found")

        returncode, _, stderr = await self._run_command(
            self._simctl_path,
            "simctl",
            "boot",
            udid,
        )

        if returncode != 0:
            raise IOSError(f"Failed to boot simulator: {stderr}", returncode, stderr)

        return True

    async def shutdown_simulator(self, udid: str) -> bool:
        """
        关闭模拟器

        Args:
            udid: 模拟器 UDID

        Returns:
            是否成功
        """
        if not self._simctl_path:
            raise IOSError("xcrun not found")

        returncode, _, stderr = await self._run_command(
            self._simctl_path,
            "simctl",
            "shutdown",
            udid,
        )

        if returncode != 0:
            raise IOSError(f"Failed to shutdown simulator: {stderr}", returncode, stderr)

        return True
