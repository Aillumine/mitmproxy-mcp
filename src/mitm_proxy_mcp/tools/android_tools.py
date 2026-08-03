"""
Android 工具

提供 Android 设备管理功能。
"""

from typing import Any

from ..android.adb_client import ADBClient, ADBError
from ..android.cert_injector import CertHelper

# 全局 ADB 客户端实例
_adb_client: ADBClient | None = None


def _get_adb() -> ADBClient:
    """获取或创建 ADB 客户端实例"""
    global _adb_client
    if _adb_client is None:
        _adb_client = ADBClient()
    return _adb_client


async def _is_emulator(serial: str) -> bool:
    """
    检测设备是否为模拟器

    Args:
        serial: 设备序列号

    Returns:
        是否为模拟器
    """
    try:
        adb = _get_adb()
        
        # 方法1: 检查序列号是否以 emulator- 开头
        if serial.startswith("emulator-"):
            return True
        
        # 方法2: 检查 ro.kernel.qemu 属性
        qemu = await adb.get_prop(serial, "ro.kernel.qemu")
        if qemu == "1":
            return True
        
        # 方法3: 检查 ro.hardware 属性
        hardware = await adb.get_prop(serial, "ro.hardware")
        if hardware and ("goldfish" in hardware.lower() or "ranchu" in hardware.lower()):
            return True
        
        # 方法4: 检查 ro.product.model
        model = await adb.get_prop(serial, "ro.product.model")
        if model and ("sdk" in model.lower() or "emulator" in model.lower()):
            return True
        
        return False
    except Exception:
        # 如果检测失败，默认返回 False
        return False


async def android_list_devices() -> dict[str, Any]:
    """
    列出所有连接的 Android 设备

    Returns:
        包含设备列表的字典
    """
    try:
        adb = _get_adb()
        devices = await adb.list_devices()

        device_list = []
        for device in devices:
            # 检测是否为模拟器
            is_emulator = await _is_emulator(device.serial)
            
            device_list.append({
                "serial": device.serial,
                "state": device.state,
                "model": device.model,
                "android_version": device.android_version,
                "is_online": device.is_online,
                "is_emulator": is_emulator,
                "device_type": "emulator" if is_emulator else "device",
            })

        return {
            "success": True,
            "devices": device_list,
            "count": len(device_list),
        }

    except ADBError as e:
        return {
            "success": False,
            "message": f"ADB error: {e}",
            "devices": [],
            "count": 0,
        }


async def android_get_device_info(serial: str) -> dict[str, Any]:
    """
    获取指定设备的详细信息

    Args:
        serial: 设备序列号

    Returns:
        包含设备详细信息的字典
    """
    try:
        adb = _get_adb()

        # 检查设备是否存在
        devices = await adb.list_devices()
        device = next((d for d in devices if d.serial == serial), None)

        if device is None:
            return {
                "success": False,
                "message": f"Device not found: {serial}",
            }

        if not device.is_online:
            return {
                "success": False,
                "message": f"Device is not online: {serial} (state: {device.state})",
            }

        # 获取更多信息
        sdk_version = await adb.get_android_version(serial)
        is_rooted = await adb.is_rooted(serial)
        is_emulator = await _is_emulator(serial)

        # 获取一些常用属性
        brand = await adb.get_prop(serial, "ro.product.brand")
        device_name = await adb.get_prop(serial, "ro.product.device")
        build_id = await adb.get_prop(serial, "ro.build.id")

        return {
            "success": True,
            "device": {
                "serial": device.serial,
                "state": device.state,
                "model": device.model,
                "android_version": device.android_version,
                "sdk_version": sdk_version,
                "is_rooted": is_rooted,
                "is_emulator": is_emulator,
                "device_type": "emulator" if is_emulator else "device",
                "brand": brand,
                "device_name": device_name,
                "build_id": build_id,
            },
        }

    except ADBError as e:
        return {
            "success": False,
            "message": f"ADB error: {e}",
        }


async def android_setup_proxy(
    serial: str,
    proxy_host: str,
    proxy_port: int,
) -> dict[str, Any]:
    """
    在设备上设置代理（通过 adb shell settings）

    注意：这种方式设置的代理只对部分应用有效

    Args:
        serial: 设备序列号
        proxy_host: 代理服务器地址
        proxy_port: 代理服务器端口

    Returns:
        包含设置状态的字典
    """
    try:
        adb = _get_adb()

        # 设置全局代理
        cmd = f"settings put global http_proxy {proxy_host}:{proxy_port}"
        exit_code, output = await adb.shell(serial, cmd)

        if exit_code != 0:
            return {
                "success": False,
                "message": f"Failed to set proxy: {output}",
            }

        return {
            "success": True,
            "message": f"Proxy set to {proxy_host}:{proxy_port}",
            "note": "This proxy setting may not work for all apps. "
                    "For better results, configure proxy in Wi-Fi settings manually.",
        }

    except ADBError as e:
        return {
            "success": False,
            "message": f"ADB error: {e}",
        }


async def android_clear_proxy(serial: str) -> dict[str, Any]:
    """
    清除设备上的代理设置

    Args:
        serial: 设备序列号

    Returns:
        包含清除状态的字典
    """
    try:
        adb = _get_adb()

        # 清除全局代理
        cmd = "settings put global http_proxy :0"
        exit_code, output = await adb.shell(serial, cmd)

        if exit_code != 0:
            return {
                "success": False,
                "message": f"Failed to clear proxy: {output}",
            }

        return {
            "success": True,
            "message": "Proxy cleared",
        }

    except ADBError as e:
        return {
            "success": False,
            "message": f"ADB error: {e}",
        }


async def android_get_proxy(serial: str) -> dict[str, Any]:
    """
    读取设备当前的全局代理设置

    Args:
        serial: 设备序列号

    Returns:
        包含代理设置的字典，未设置代理时 proxy 为 None
    """
    try:
        adb = _get_adb()

        # shell_with_exit_code rather than shell: the latter returns the adb
        # process exit code, which is 0 even when the on-device command fails.
        #
        # 用 shell_with_exit_code 而非 shell：后者返回的是 adb 进程的退出码，
        # 设备上的命令失败时它依然是 0，无法用来判断成败。
        exit_code, output = await adb.shell_with_exit_code(
            serial, "settings get global http_proxy"
        )
        raw = output.strip()

        if exit_code != 0:
            return {"success": False, "message": f"读取代理设置失败: {raw}", "raw": raw}

        # Android writes ":0" or "null" when no proxy is configured; treating
        # either as a real proxy would make the self-check report a false pass.
        #
        # 未配置代理时 Android 写入的是 ":0" 或 "null"，若当成真代理，
        # 连接自检会误报「已配置」。
        if raw in ("", ":0", "null"):
            return {
                "success": True,
                "proxy": None,
                "host": None,
                "port": None,
                "raw": raw,
            }

        host, _, port_str = raw.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            host, port = raw, None

        return {
            "success": True,
            "proxy": raw,
            "host": host or None,
            "port": port,
            "raw": raw,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}


# Certificate store paths. Android 14 (SDK 34) moved the system store into the
# Conscrypt APEX, so the legacy path alone is no longer conclusive.
#
# 证书凭据库路径。Android 14（SDK 34）起系统库迁到了 Conscrypt APEX，
# 只查旧路径已经不足以判断。
_USER_STORE = "/data/misc/user/0/cacerts-added"
_SYSTEM_STORE = "/system/etc/security/cacerts"
_APEX_STORE = "/apex/com.android.conscrypt/cacerts"


async def _probe_cert(adb, serial: str, store: str, filename: str) -> str:
    """
    探测某个凭据库里是否存在指定证书

    Returns:
        present | absent | unknown（无读权限时为 unknown）
    """
    path = f"{store}/{filename}"
    exit_code, output = await adb.shell_with_exit_code(
        serial, f"test -f {path} && echo EXISTS"
    )

    if exit_code == 0 and "EXISTS" in output:
        return "present"

    # A permission error is not the same as "no certificate": the user store is
    # unreadable without root, and reporting absent there would tell the user to
    # install a certificate that may already be installed.
    #
    # 权限错误不等于「没有证书」：用户库无 root 读不了，误报 absent 会让用户
    # 去重复安装一个可能已经装好的证书。
    if "Permission denied" in output or "denied" in output.lower():
        return "unknown"

    return "absent"


async def android_cert_status(serial: str) -> dict[str, Any]:
    """
    检测 mitmproxy CA 证书在设备上的安装状态

    Args:
        serial: 设备序列号

    Returns:
        包含三个凭据库状态、是否被 App 信任、以及处理建议的字典
    """
    try:
        cert_info = CertHelper().get_cert_info()
    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 mitmproxy CA 证书。请先启动代理以生成证书。",
        }

    filename = cert_info.filename

    try:
        adb = _get_adb()
        sdk_version = await adb.get_android_version(serial)
        is_rooted = await adb.is_rooted(serial)

        stores = {
            "user": await _probe_cert(adb, serial, _USER_STORE, filename),
            "system": await _probe_cert(adb, serial, _SYSTEM_STORE, filename),
            "apex": await _probe_cert(adb, serial, _APEX_STORE, filename),
        }

        # Only the system stores make apps trust the CA. A certificate sitting in
        # the user store is invisible to any app targeting Android 7+ unless that
        # app opted in via networkSecurityConfig.
        #
        # 只有系统库能让 App 信任 CA。装在用户库里的证书，对任何
        # targetSdk >= 24 的 App 都是不可见的，除非该 App 主动在
        # networkSecurityConfig 里声明信任用户证书。
        trusted = stores["system"] == "present" or stores["apex"] == "present"

        if trusted:
            advice = "证书已在系统凭据库，App 会信任该 CA。"
        elif stores["user"] == "present":
            advice = (
                "证书只在用户凭据库，Android 7+ 的 App 默认不信任。"
                "请调用 android_inject_system_cert 注入系统库"
                "（需要 root 或使用模拟器）。"
            )
        elif stores["user"] == "unknown":
            advice = (
                "设备未 root，无法读取用户凭据库状态。"
                "若确认未安装，请先调用 android_push_cert 推送证书后手动安装，"
                "再用 android_inject_system_cert 注入系统库。"
            )
        else:
            advice = (
                "设备上未安装证书。请先调用 android_push_cert 推送到 "
                "/sdcard/Download，再在系统设置里安装。"
            )

        return {
            "success": True,
            "cert_filename": filename,
            "sdk_version": sdk_version,
            "is_rooted": is_rooted,
            "stores": stores,
            "trusted_by_apps": trusted,
            "advice": advice,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
