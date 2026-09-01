"""
MITM Proxy 启动脚本

交互式启动代理服务，使用 mitmproxy 原生命令。
支持 Android 和 iOS 设备。

命令：
  mitmproxy-start              只启动代理
  mitmproxy-start --setup-proxy 启动代理并自动设置 Mac WiFi 代理
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

from ..control.runtime import is_pid_alive, read_runtime

# 配置 loguru
logger.remove()
logger.add(
    sys.stderr,
    format="<level>{message}</level>",
    level="INFO",
    colorize=True,
)


def get_local_ip() -> str:
    """获取本机局域网 IP（优先选择私有IP段：192.168.x.x, 10.x.x.x, 172.16-31.x.x）"""
    import ipaddress
    
    def is_private_ip(ip_str: str) -> bool:
        """检查是否是私有IP地址"""
        try:
            ip = ipaddress.IPv4Address(ip_str)
            # 检查是否是私有IP段
            return (
                ip.is_private or
                ip.is_link_local or
                (ip_str.startswith("192.168.")) or
                (ip_str.startswith("10.")) or
                (ip_str.startswith("172.16.") or ip_str.startswith("172.17.") or
                 ip_str.startswith("172.18.") or ip_str.startswith("172.19.") or
                 ip_str.startswith("172.20.") or ip_str.startswith("172.21.") or
                 ip_str.startswith("172.22.") or ip_str.startswith("172.23.") or
                 ip_str.startswith("172.24.") or ip_str.startswith("172.25.") or
                 ip_str.startswith("172.26.") or ip_str.startswith("172.27.") or
                 ip_str.startswith("172.28.") or ip_str.startswith("172.29.") or
                 ip_str.startswith("172.30.") or ip_str.startswith("172.31."))
            )
        except ValueError:
            return False
    
    # 方法1: 遍历系统网络接口获取私有IP（macOS）
    try:
        import re
        # 优先尝试 ip 命令（Linux，macOS 通常没有）
        try:
            result = subprocess.run(
                ["ip", "addr", "show"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if "inet " in line and "127.0.0.1" not in line:
                    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                    if match:
                        ip_str = match.group(1)
                        if is_private_ip(ip_str):
                            return ip_str
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # macOS 回退到 ifconfig
            try:
                result = subprocess.run(
                    ["ifconfig"], capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split("\n"):
                    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                    if match:
                        ip_str = match.group(1)
                        if ip_str != "127.0.0.1" and is_private_ip(ip_str):
                            return ip_str
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    except Exception:
        pass
    
    # 方法2: 通过连接外部地址获取本机IP（原方法）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_str = s.getsockname()[0]
        s.close()
        # 如果获取到的是私有IP，直接返回
        if is_private_ip(ip_str):
            return ip_str
        # 如果不是私有IP，继续尝试其他方法
    except Exception:
        pass
    
    # 方法3: 尝试连接本地网关
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))  # 常见网关地址
        ip_str = s.getsockname()[0]
        s.close()
        if is_private_ip(ip_str):
            return ip_str
    except Exception:
        pass
    
    # 方法4: 如果都失败，返回回环地址
    return "127.0.0.1"


def get_wifi_service_name() -> str | None:
    """获取 Mac 当前 Wi-Fi 网络服务名称"""
    try:
        # 获取所有网络服务
        result = subprocess.run(
            ["networksetup", "-listallnetworkservices"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            # 跳过标题行和被禁用的服务（带 * 前缀）
            if line.startswith("An asterisk") or line.startswith("*"):
                continue
            # 查找 Wi-Fi 服务
            if line.lower() in ("wi-fi", "wifi"):
                return line
        # 如果没找到精确匹配，返回默认值
        return "Wi-Fi"
    except Exception:
        return "Wi-Fi"


def enable_mac_proxy(port: int) -> bool:
    """
    开启 Mac 系统 Wi-Fi 代理（HTTP + HTTPS）

    Args:
        port: 代理端口号

    Returns:
        是否成功设置
    """
    service = get_wifi_service_name() or "Wi-Fi"
    host = "127.0.0.1"
    success = True

    try:
        # 设置 HTTP 代理
        result = subprocess.run(
            ["networksetup", "-setwebproxy", service, host, str(port)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.warning(f"    设置 HTTP 代理失败: {result.stderr.strip()}")
            success = False

        # 设置 HTTPS 代理
        result = subprocess.run(
            ["networksetup", "-setsecurewebproxy", service, host, str(port)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.warning(f"    设置 HTTPS 代理失败: {result.stderr.strip()}")
            success = False

        return success
    except Exception as e:
        logger.warning(f"    设置 Mac 代理出错: {e}")
        return False


def disable_mac_proxy() -> bool:
    """
    关闭 Mac 系统 Wi-Fi 代理（HTTP + HTTPS）

    Returns:
        是否成功关闭
    """
    service = get_wifi_service_name() or "Wi-Fi"
    success = True

    try:
        # 关闭 HTTP 代理
        result = subprocess.run(
            ["networksetup", "-setwebproxystate", service, "off"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.warning(f"    关闭 HTTP 代理失败: {result.stderr.strip()}")
            success = False

        # 关闭 HTTPS 代理
        result = subprocess.run(
            ["networksetup", "-setsecurewebproxystate", service, "off"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.warning(f"    关闭 HTTPS 代理失败: {result.stderr.strip()}")
            success = False

        return success
    except Exception as e:
        logger.warning(f"    关闭 Mac 代理出错: {e}")
        return False


def resolve_db_paths() -> tuple[Path, Path]:
    """返回 (traffic_db, mock_db)，优先复用运行中控制服务的数据库路径。"""
    from ..core.mock_store import MockStore
    from ..core.sqlite_store import SQLiteTrafficStore

    info = read_runtime()
    if info is not None and is_pid_alive(info.pid):
        return Path(info.traffic_db), Path(info.mock_db)
    return SQLiteTrafficStore.get_default_path(), MockStore.get_default_path()


def check_port_available(port: int) -> bool:
    """检查端口是否可用"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", port))
        s.close()
        return True
    except OSError:
        return False


def kill_port_process(port: int) -> bool:
    """关闭占用指定端口的进程"""
    try:
        # 使用 lsof 查找占用端口的进程
        result = subprocess.run(
            ["lsof", "-t", "-i", f":{port}"],
            capture_output=True,
            text=True
        )
        pids = [pid.strip() for pid in result.stdout.strip().split('\n') if pid.strip()]
        if not pids:
            return False
        
        # 先尝试 SIGTERM（优雅退出）
        for pid in pids:
            try:
                subprocess.run(["kill", pid], capture_output=True, timeout=1)
            except Exception:
                pass
        
        # 等待一下
        time.sleep(0.5)
        
        # 再次检查，如果还有进程占用端口，强制 kill
        result = subprocess.run(
            ["lsof", "-t", "-i", f":{port}"],
            capture_output=True,
            text=True
        )
        remaining_pids = [pid.strip() for pid in result.stdout.strip().split('\n') if pid.strip()]
        
        if remaining_pids:
            # 强制 kill
            for pid in remaining_pids:
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=1)
                except Exception:
                    pass
            time.sleep(0.5)
        
        # 最终检查端口是否可用
        return check_port_available(port)
    except Exception:
        return False


def mitmdump_args(port: int, addon_path: str) -> list[str]:
    """mitmdump 启动参数。大 body 流式转发，避免整包进内存；不影响 JSON/WSS。"""
    return [
        "--listen-host", "0.0.0.0",
        "-p", str(port),
        "-s", addon_path,
        "--set", "ssl_insecure=true",
        "--set", "stream_large_bodies=1m",
    ]


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="MITM Proxy MCP 启动脚本")
    parser.add_argument("--port", type=int, default=8888, help="监听端口 (默认: 8888)")
    parser.add_argument(
        "--setup-proxy",
        action="store_true",
        default=False,
        help="自动设置 Mac Wi-Fi 系统代理（启动时开启，关闭时恢复）",
    )
    args = parser.parse_args()

    # 标记是否需要在退出时恢复代理
    proxy_enabled = False

    # ========== 欢迎界面 ==========
    logger.opt(colors=True).info("<magenta>╔════════════════════════════════════════════════════════════╗</magenta>")
    logger.opt(colors=True).info("<magenta>║            🚀 MITM Proxy MCP 启动向导                     ║</magenta>")
    logger.opt(colors=True).info("<magenta>╚════════════════════════════════════════════════════════════╝</magenta>")

    # ========== 环境检测 ==========
    logger.opt(colors=True).info(f"\n<cyan>{'═' * 60}</cyan>")
    logger.opt(colors=True).info("<cyan>  环境检测</cyan>")
    logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>\n")

    # 端口检测
    if check_port_available(args.port):
        logger.opt(colors=True).success(f"    ✓ 端口 {args.port} 可用")
    else:
        logger.opt(colors=True).warning(f"    ⚠️  端口 {args.port} 已被占用")
        try:
            answer = input("\n    是否关闭占用该端口的进程？(y/N): ").strip().lower()
            if answer == 'y':
                if kill_port_process(args.port):
                    logger.opt(colors=True).success(f"    ✓ 端口 {args.port} 已释放")
                else:
                    logger.error(f"    ✗ 无法释放端口")
                    sys.exit(1)
            else:
                sys.exit(1)
        except (EOFError, KeyboardInterrupt):
            sys.exit(1)

    local_ip = get_local_ip()

    # ========== 显示配置信息 ==========
    logger.opt(colors=True).info(f"\n<cyan>{'═' * 60}</cyan>")
    logger.opt(colors=True).info("<cyan>  设备配置</cyan>")
    logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>\n")

    logger.info("    设备代理设置:")
    logger.info("")
    logger.info(f"       ┌─────────────────────────────────┐")
    logger.opt(colors=True).info(f"       │  服务器: <cyan>{local_ip:^20}</cyan> │")
    logger.opt(colors=True).info(f"       │  端  口: <cyan>{args.port:^20}</cyan> │")
    logger.info(f"       └─────────────────────────────────┘")
    logger.info("")
    logger.opt(colors=True).info("    📱 iOS 模拟器证书安装:")
    logger.info("    1. 在模拟器的 Safari 中访问: http://mitm.it")
    logger.info("    2. 点击 'Apple' 图标下载证书")
    logger.info("    3. 设置 → 通用 → VPN与设备管理 → 安装描述文件")
    logger.info("    4. 设置 → 通用 → 关于本机 → 证书信任设置 → 启用 mitmproxy")
    logger.info("")
    logger.opt(colors=True).info("    💻 Mac 系统代理:")
    logger.info("    默认不修改、不覆盖本机系统代理。")
    logger.info("    抓手机请在设备 Wi-Fi 里填写上面的服务器和端口。")
    logger.info("")

    # ========== 设置 Mac 系统代理（可选） ==========
    if args.setup_proxy:
        logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>")
        logger.opt(colors=True).info("<cyan>  设置 Mac Wi-Fi 系统代理</cyan>")
        logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>\n")

        if enable_mac_proxy(args.port):
            proxy_enabled = True
            logger.opt(colors=True).success(f"    ✓ 已设置 HTTP  代理 → 127.0.0.1:{args.port}")
            logger.opt(colors=True).success(f"    ✓ 已设置 HTTPS 代理 → 127.0.0.1:{args.port}")
        else:
            logger.opt(colors=True).warning("    ⚠️  代理设置可能未完全生效，请检查系统偏好设置")
        logger.info("")

    # ========== 启动 mitmproxy ==========
    logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>")
    logger.opt(colors=True).info("<cyan>  启动代理 (Ctrl+C 停止)</cyan>")
    logger.opt(colors=True).info(f"<cyan>{'═' * 60}</cyan>\n")

    process = None
    try:
        db_path, mock_db_path = resolve_db_paths()
        # 重启代理不清空历史流量，避免 WSS/接口分组在界面上「消失」。
        # 需要重新开始时用控制台的 Clear。

        # 通过环境变量告诉 addon 该用哪个库，再把真实模块路径交给 mitmdump。
        # 这段原本是写到 /tmp 的 200 行 f-string，既不能 import 也无法测试。
        addon_path = str(
            Path(__file__).resolve().parent.parent / "addon" / "traffic_addon.py"
        )
        os.environ["MITMPROXY_DB_PATH"] = str(db_path)
        os.environ["MITMPROXY_MOCK_DB_PATH"] = str(mock_db_path)

        logger.opt(colors=True).info(f"    📂 流量保存: <dim>{db_path}</dim>")
        logger.info("")

        # 启动 mitmdump
        # 使用 sys.executable 运行 mitmdump 脚本，忽略 shebang 中的错误路径
        # 监听 0.0.0.0 以允许 iOS 模拟器和其他设备连接
        mitmdump_cmd = shutil.which("mitmdump")
        if mitmdump_cmd:
            cmd = [sys.executable, mitmdump_cmd, *mitmdump_args(args.port, addon_path)]
        else:
            from mitmproxy.tools.main import mitmdump
            sys.argv = ["mitmdump", *mitmdump_args(args.port, addon_path)]
            mitmdump()
            return
        
        process = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=os.environ.copy(),
        )

        # 等待退出
        try:
            process.wait()
        except KeyboardInterrupt:
            # 如果 process.wait() 被中断，进程可能还在运行
            raise

    except KeyboardInterrupt:
        logger.info("\n")
        logger.warning("    正在停止代理...")
        
        # 首先尝试通过 process 对象终止
        if 'process' in locals() and process:
            try:
                # 检查进程是否还在运行
                if process.poll() is None:
                    # 先尝试优雅退出
                    process.terminate()
                    # 等待最多2秒
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        # 如果2秒后还没退出，强制kill
                        logger.warning("    强制终止进程...")
                        process.kill()
                        process.wait(timeout=1)
            except Exception as e:
                logger.warning(f"    停止进程时出错: {e}")
        
        # 等待一下，确保进程完全退出
        time.sleep(0.5)
        
        # 确保端口被释放（通过端口查找并杀死所有占用端口的进程）
        try:
            killed = kill_port_process(args.port)
            if killed:
                logger.opt(colors=True).success("    ✓ 端口已释放")
            else:
                # 再次检查端口是否真的被占用
                if check_port_available(args.port):
                    logger.info("    ℹ️  端口未被占用")
                else:
                    logger.warning("    ⚠️  端口可能仍被占用，请手动检查")
        except Exception as e:
            logger.warning(f"    清理端口时出错: {e}")
        
        # 如果启动时设置了 Mac 代理，关闭时自动恢复
        if proxy_enabled:
            try:
                if disable_mac_proxy():
                    logger.opt(colors=True).success("    ✓ Mac Wi-Fi 系统代理已关闭")
                else:
                    logger.warning("    ⚠️  Mac 代理可能未完全关闭，请手动检查")
            except Exception as e:
                logger.warning(f"    关闭 Mac 代理时出错: {e}")
        
        logger.opt(colors=True).success("    ✓ 代理已停止")
        logger.info("")


if __name__ == "__main__":
    main()
