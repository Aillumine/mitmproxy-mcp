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
            "system": await _probe_cert(adb, serial, _SYSTEM_STORE, filename),
            "apex": await _probe_cert(adb, serial, _APEX_STORE, filename),
        }

        if is_rooted:
            stores["user"] = await _probe_cert(adb, serial, _USER_STORE, filename)
        else:
            stores["user"] = "unknown"

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


async def android_push_cert(serial: str) -> dict[str, Any]:
    """
    推送 mitmproxy CA 证书到设备的 /sdcard/Download

    推送只是第一步，用户仍需在系统设置里手动安装，或调用
    android_inject_system_cert 注入系统凭据库。

    Args:
        serial: 设备序列号

    Returns:
        包含设备路径与安装指引的字典
    """
    try:
        adb = _get_adb()
        helper = CertHelper(adb)

        cert_info = helper.get_cert_info()
        remote_path = await helper.push_cert_to_device(serial)

        return {
            "success": True,
            "remote_path": remote_path,
            "cert_filename": cert_info.filename,
            "instructions": helper.get_install_instructions(cert_info),
        }

    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 mitmproxy CA 证书。请先启动代理以生成证书。",
        }
    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}


async def _run_root_steps(adb, serial: str, commands: list[str]) -> tuple[bool, str]:
    """
    按顺序执行一组 root 命令，任一失败即中断

    Returns:
        (是否全部成功, 失败时的设备输出)
    """
    for command in commands:
        exit_code, output = await adb.root_shell(serial, command)
        if exit_code != 0:
            return False, output.strip()
    return True, ""


async def android_inject_system_cert(serial: str) -> dict[str, Any]:
    """
    把 mitmproxy CA 证书注入设备的系统凭据库

    Android 13 及以下重新挂载 /system 写入，重启后仍然有效；
    Android 14+ 的系统库位于只读的 Conscrypt APEX，只能用 tmpfs 覆盖，
    重启后失效。

    Args:
        serial: 设备序列号

    Returns:
        包含注入方式、是否持久、以及限制说明的字典
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

        if not await adb.is_rooted(serial):
            return {
                "success": False,
                "message": (
                    "注入系统凭据库需要 root 权限，该设备未 root。"
                    "可改用 Android 模拟器（可写系统分区），"
                    "或在 App 的 debug 构建里通过 networkSecurityConfig 信任用户证书。"
                ),
            }

        sdk_version = await adb.get_android_version(serial)

        # Push first so the certificate exists on-device regardless of which
        # injection path we take below.
        #
        # 先推送，保证证书在设备上存在，两条注入路径都要用到它。
        helper = CertHelper(adb)
        remote_path = await helper.push_cert_to_device(serial)

        if sdk_version >= 34:
            # Android 14 moved the system trust store into a read-only APEX.
            # Overlaying it with tmpfs is the only root-only option, and it lives
            # in memory: a reboot drops it.
            #
            # Android 14 把系统信任库挪进了只读的 APEX。root 环境下只能用 tmpfs
            # 覆盖，而这个覆盖在内存里，重启即失效。
            commands = [
                f"mount -t tmpfs tmpfs {_APEX_STORE}",
                f"cp {_SYSTEM_STORE}/* {_APEX_STORE}/ 2>/dev/null || true",
                f"cp {remote_path} {_APEX_STORE}/{filename}",
                f"chmod 644 {_APEX_STORE}/{filename}",
                f"chown root:root {_APEX_STORE}/{filename}",
                f"chcon u:object_r:system_file:s0 {_APEX_STORE}/{filename}",
            ]
            ok, output = await _run_root_steps(adb, serial, commands)
            if not ok:
                return {"success": False, "message": f"APEX 注入失败: {output}"}

            return {
                "success": True,
                "method": "apex_tmpfs",
                "persistent": False,
                "message": f"证书已注入 {_APEX_STORE}/{filename}",
                "warning": (
                    "该覆盖位于内存，设备重启后失效，需要重新注入；"
                    "且只对挂载之后启动的进程生效，请重启目标 App。"
                ),
            }

        commands = [
            "mount -o rw,remount /system",
            f"cp {remote_path} {_SYSTEM_STORE}/{filename}",
            f"chmod 644 {_SYSTEM_STORE}/{filename}",
            f"chown root:root {_SYSTEM_STORE}/{filename}",
            "mount -o ro,remount /system",
        ]
        ok, output = await _run_root_steps(adb, serial, commands)
        if not ok:
            return {"success": False, "message": f"系统分区写入失败: {output}"}

        return {
            "success": True,
            "method": "system_remount",
            "persistent": True,
            "message": f"证书已注入 {_SYSTEM_STORE}/{filename}",
            "warning": "请重启目标 App 以让新的信任链生效。",
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}


async def android_reverse_proxy(serial: str, port: int = 8888) -> dict[str, Any]:
    """
    通过 adb reverse 让设备经由 127.0.0.1 访问主机代理

    相比全局代理，这种方式不要求设备与主机在同一局域网，
    也不受 Wi-Fi 切换影响，是真机抓包更可靠的接法。

    Args:
        serial: 设备序列号
        port: 主机上的代理端口

    Returns:
        包含代理地址与隧道信息的字典
    """
    try:
        adb = _get_adb()
        endpoint = f"tcp:{port}"

        # Establish the tunnel before pointing the device at 127.0.0.1. Doing it
        # the other way round would leave the device with a proxy that routes
        # nowhere if the tunnel fails — i.e. no network at all.
        #
        # 必须先建隧道再改代理指向。反过来的话，一旦建隧道失败，设备的代理会
        # 指向一个不存在的本地端口，结果是整机断网。
        await adb.reverse(serial, endpoint, endpoint)

        exit_code, output = await adb.shell_with_exit_code(
            serial, f"settings put global http_proxy 127.0.0.1:{port}"
        )
        if exit_code != 0:
            return {"success": False, "message": f"设置代理失败: {output.strip()}"}

        return {
            "success": True,
            "proxy": f"127.0.0.1:{port}",
            "remote": endpoint,
            "local": endpoint,
        }

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}


async def android_reverse_proxy_remove(serial: str, port: int = 8888) -> dict[str, Any]:
    """
    移除 adb reverse 代理接入

    Args:
        serial: 设备序列号
        port: 主机上的代理端口

    Returns:
        包含移除状态的字典
    """
    try:
        adb = _get_adb()

        # Clear the proxy first: dropping the tunnel while the device still points
        # at 127.0.0.1 would black-hole its traffic.
        #
        # 先清代理再拆隧道：如果设备还指着 127.0.0.1 就先拆隧道，
        # 这段时间内设备流量会被黑洞掉。
        exit_code, output = await adb.shell_with_exit_code(
            serial, "settings put global http_proxy :0"
        )
        if exit_code != 0:
            return {"success": False, "message": f"清除代理失败: {output.strip()}"}

        removed = await adb.reverse_remove(serial, f"tcp:{port}")
        if not removed:
            return {
                "success": False,
                "message": f"移除 reverse 隧道失败（tcp:{port}）",
            }

        return {"success": True, "message": f"已移除 reverse 代理（tcp:{port}）"}

    except ADBError as e:
        return {"success": False, "message": f"ADB error: {e}"}
