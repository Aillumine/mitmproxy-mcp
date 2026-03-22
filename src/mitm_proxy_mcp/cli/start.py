"""
MITM Proxy 启动脚本

交互式启动代理服务，使用 mitmproxy 原生命令。
支持 Android 和 iOS 设备。

命令：
  mitmproxy-start              只启动代理
  mitmproxy-start --setup-proxy 启动代理并自动设置 Mac WiFi 代理
"""

import socket
import subprocess
import sys
import time
import shutil

from loguru import logger

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
    logger.opt(colors=True).info("    💻 Mac 系统代理设置:")
    logger.info("    系统设置 → 网络 → Wi-Fi → 高级 → 代理")
    logger.info("    网页代理(HTTP): 127.0.0.1:8888")
    logger.info("    安全网页代理(HTTPS): 127.0.0.1:8888")
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
        # 直接使用 mitmdump，流量会保存到 SQLite
        from ..core.sqlite_store import SQLiteTrafficStore
        from ..core.mock_store import MockStore

        db_path = SQLiteTrafficStore.get_default_path()
        mock_db_path = MockStore.get_default_path()
        store = SQLiteTrafficStore(db_path)
        store.clear()

        # 创建 addon 脚本来保存流量到 SQLite + mock 拦截
        addon_script = f'''
import json
import re
import time
from pathlib import Path
import sqlite3
from urllib.parse import urlparse
from mitmproxy import http

DB_PATH = "{db_path}"
MOCK_DB_PATH = "{mock_db_path}"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS traffic (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            domain TEXT NOT NULL,
            status INTEGER NOT NULL,
            resource_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            time_ms REAL NOT NULL,
            request_headers TEXT,
            request_body BLOB,
            request_body_size INTEGER DEFAULT 0,
            response_headers TEXT,
            response_body BLOB,
            timing TEXT,
            error TEXT
        )
    """)
    conn.commit()
    conn.close()

def infer_resource_type(mime_type: str, url: str) -> str:
    """推断资源类型"""
    if not mime_type:
        mime_type = ""
    mime_lower = mime_type.split(";")[0].strip().lower()
    
    try:
        parsed = urlparse(url)
        ext = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
    except:
        ext = ""
    
    if "text/html" in mime_lower or ext in (".html", ".htm"):
        return "Document"
    if "text/css" in mime_lower or ext == ".css":
        return "Stylesheet"
    if mime_lower.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico"):
        return "Image"
    if "javascript" in mime_lower or ext in (".js", ".mjs"):
        return "Script"
    if "json" in mime_lower or "xml" in mime_lower or ext in (".json", ".xml"):
        return "XHR"
    if mime_lower.startswith("application/"):
        return "XHR"
    
    return "Other"

def match_url(url, pattern, match_type):
    """检查 URL 是否匹配 mock 规则"""
    if match_type == "exact":
        return url == pattern
    elif match_type == "regex":
        try:
            return bool(re.search(pattern, url))
        except re.error:
            return False
    else:  # contains
        if "*" in pattern:
            regex_pattern = re.escape(pattern).replace(r"\\*", ".*")
            try:
                return bool(re.search(regex_pattern, url))
            except re.error:
                return False
        return pattern in url

def find_matching_mock(url, method):
    """查找匹配的 mock 规则"""
    if not Path(MOCK_DB_PATH).exists():
        return None
    try:
        conn = sqlite3.connect(MOCK_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM mock_rules WHERE enabled = 1 ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        
        for row in rows:
            rule_method = row["method"] or ""
            if rule_method and rule_method != method.upper():
                continue
            if match_url(url, row["url_pattern"], row["match_type"] or "contains"):
                return dict(row)
        return None
    except Exception as e:
        print(f"[Mock] Error reading rules: {{e}}")
        return None

def increment_mock_hit(rule_id):
    """递增 mock 命中计数"""
    try:
        conn = sqlite3.connect(MOCK_DB_PATH, timeout=5)
        conn.execute(
            "UPDATE mock_rules SET hit_count = hit_count + 1 WHERE id = ?",
            (rule_id,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

init_db()
counter = [0]

def request(flow):
    """在请求阶段检查 mock 规则，匹配则直接返回 mock 响应"""
    url = flow.request.pretty_url
    method = flow.request.method
    
    mock_rule = find_matching_mock(url, method)
    if mock_rule is None:
        return
    
    delay_ms = mock_rule.get("delay_ms", 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    headers = json.loads(mock_rule.get("response_headers", "{{}}"))
    headers["X-Mock-Rule"] = mock_rule["id"]
    
    body = (mock_rule.get("response_body", "") or "").encode("utf-8")
    
    flow.response = http.Response.make(
        mock_rule.get("status_code", 200),
        body,
        headers,
    )
    
    increment_mock_hit(mock_rule["id"])
    
    counter[0] += 1
    print(f"[{{counter[0]}}] [MOCK] {{method}} {{url[:80]}} -> {{mock_rule.get('status_code', 200)}} (rule: {{mock_rule['name']}})")

def response(flow):
    counter[0] += 1
    record_id = f"req-{{counter[0]}}"

    if not flow.response:
        return
    
    try:
        url = flow.request.pretty_url
        domain = flow.request.host
        
        content_type = flow.response.headers.get("content-type", "")
        resource_type = infer_resource_type(content_type, url)
        
        if flow.response.timestamp_end and flow.request.timestamp_start:
            time_ms = (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
        else:
            time_ms = 0.0

        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO traffic (
                    id, timestamp, method, url, domain, status,
                    resource_type, size, time_ms, request_headers,
                    request_body, response_headers, response_body, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                time.time(),
                flow.request.method,
                url,
                domain,
                flow.response.status_code,
                resource_type,
                len(flow.response.content) if flow.response.content else 0,
                time_ms,
                json.dumps(dict(flow.request.headers)),
                flow.request.content,
                json.dumps(dict(flow.response.headers)),
                flow.response.content,
                None
            ))
            conn.commit()
            is_mock = flow.response.headers.get("X-Mock-Rule", "")
            tag = " [MOCK]" if is_mock else ""
            print(f"[{{counter[0]}}]{{tag}} {{flow.request.method}} {{url[:80]}}")
        finally:
            conn.close()
    except Exception as e:
        print(f"Error saving traffic: {{e}}")
'''

        # 写入临时 addon 脚本
        addon_path = "/tmp/mitmproxy_addon.py"
        with open(addon_path, "w") as f:
            f.write(addon_script)

        logger.opt(colors=True).info(f"    📂 流量保存: <dim>{db_path}</dim>")
        logger.info("")

        # 启动 mitmdump
        # 使用 sys.executable 运行 mitmdump 脚本，忽略 shebang 中的错误路径
        # 监听 0.0.0.0 以允许 iOS 模拟器和其他设备连接
        mitmdump_cmd = shutil.which("mitmdump")
        if mitmdump_cmd:
            # 使用 sys.executable 运行脚本，忽略 shebang 中的错误路径
            # 添加 --listen-host 0.0.0.0 让代理监听所有网络接口
            # 添加 --set ssl_insecure=true 允许弱证书和自签名证书（仅用于抓包）
            cmd = [
                sys.executable, mitmdump_cmd,
                "--listen-host", "0.0.0.0",
                "-p", str(args.port),
                "-s", addon_path,
                "--set", "ssl_insecure=true",
            ]
        else:
            # 如果找不到 mitmdump，直接使用 Python API 调用
            from mitmproxy.tools.main import mitmdump
            # 直接调用 mitmdump 函数（同步调用，会阻塞）
            sys.argv = [
                "mitmdump",
                "--listen-host", "0.0.0.0",
                "-p", str(args.port),
                "-s", addon_path,
                "--set", "ssl_insecure=true",
            ]
            mitmdump()
            return
        
        process = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
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
        if not proxy_enabled:
            logger.warning("    ⚠️  记得关闭 Mac 系统代理设置!")
        logger.info("")


if __name__ == "__main__":
    main()
