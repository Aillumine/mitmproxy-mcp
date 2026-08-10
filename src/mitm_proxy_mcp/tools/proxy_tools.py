"""
代理工具

提供证书信息查询、代理启动/停止功能。
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..android.cert_injector import CertHelper
from ..control.capture_context import resolve_setup_proxy
from ..core.sqlite_store import SQLiteTrafficStore

# PID 文件路径（用于跟踪代理进程）
PID_FILE = Path("/tmp/mitmproxy-mcp.pid")


def get_cert_info() -> dict[str, Any]:
    """
    获取 CA 证书信息和安装指南

    Returns:
        包含证书信息和安装指南的字典
    """
    helper = CertHelper()

    try:
        cert_info = helper.get_cert_info()
        instructions = helper.get_install_instructions(cert_info)

        return {
            "success": True,
            "cert_path": cert_info.pem_path,
            "cert_hash": cert_info.hash,
            "cert_filename": cert_info.filename,
            "install_instructions": instructions,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "message": "CA 证书未找到。请先运行: uv run mitmproxy-start",
            "install_instructions": helper.get_install_instructions(),
        }


def _get_project_root() -> Path | None:
    """获取项目根目录"""
    # 从当前文件位置向上查找 pyproject.toml
    current = Path(__file__).resolve()
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    # 如果找不到，返回 None（让 uv 自己处理）
    return None


def _save_pid(pid: int) -> None:
    """保存进程 PID 到文件"""
    try:
        PID_FILE.write_text(str(pid))
    except Exception:
        pass


def _read_pid() -> int | None:
    """从文件读取进程 PID"""
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            # 检查进程是否还在运行
            try:
                os.kill(pid, 0)  # 发送信号 0 检查进程是否存在
                return pid
            except (OSError, ProcessLookupError):
                # 进程不存在，删除 PID 文件
                PID_FILE.unlink(missing_ok=True)
                return None
    except Exception:
        pass
    return None


def _find_proxy_process_by_port(port: int = 8888) -> int | None:
    """通过端口查找代理进程 PID"""
    try:
        result = subprocess.run(
            ["lsof", "-t", "-i", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(p.strip()) for p in result.stdout.strip().split("\n") if p.strip()]
            # 返回第一个找到的 PID
            return pids[0] if pids else None
    except Exception:
        pass
    return None


def proxy_start(port: int = 8888, setup_proxy: bool = False) -> dict[str, Any]:
    """
    启动代理服务（后台运行）

    Args:
        port: 代理端口，默认 8888
        setup_proxy: 是否自动设置 Mac Wi-Fi 系统代理，默认 False

    Returns:
        包含启动状态的字典
    """
    setup_proxy, setup_proxy_block_reason = resolve_setup_proxy(setup_proxy)

    def with_setup_proxy_guard(result: dict[str, Any]) -> dict[str, Any]:
        result["setup_proxy"] = setup_proxy
        if setup_proxy_block_reason:
            message = result.get("message", "")
            result["message"] = (
                f"{message}\n⚠️  {setup_proxy_block_reason}"
                if message
                else setup_proxy_block_reason
            )
            result["setup_proxy_blocked"] = True
        return result

    # 检查是否已经在运行
    existing_pid = _read_pid() or _find_proxy_process_by_port(port)
    if existing_pid:
        return with_setup_proxy_guard({
            "success": False,
            "message": f"代理已在运行（PID: {existing_pid}）。请先使用 proxy_stop 停止代理。",
            "pid": existing_pid,
        })

    # 检查端口是否被占用
    try:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        if result == 0:
            # 端口被占用，尝试通过端口查找进程
            pid = _find_proxy_process_by_port(port)
            if pid:
                return with_setup_proxy_guard({
                    "success": False,
                    "message": f"端口 {port} 已被占用（PID: {pid}）。请先使用 proxy_stop 停止代理。",
                    "pid": pid,
                })
            return with_setup_proxy_guard({
                "success": False,
                "message": f"端口 {port} 已被占用，但无法确定进程。请手动检查。",
            })
    except Exception:
        pass

    # 查找 uv 命令
    import shutil

    uv_cmd = shutil.which("uv")
    if not uv_cmd:
        return with_setup_proxy_guard({
            "success": False,
            "message": "未找到 uv 命令。请确保已安装 uv: https://github.com/astral-sh/uv",
        })

    # 获取项目根目录（可选）
    project_root = _get_project_root()

    # 构建启动命令
    # 使用 uv run，它会自动找到项目目录（通过 pyproject.toml）
    cmd = [uv_cmd, "run", "mitmproxy-start", "--port", str(port)]
    if setup_proxy:
        cmd.append("--setup-proxy")

    try:
        # 将错误输出保存到临时文件，以便调试
        import tempfile

        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".log", prefix="mitmproxy-"
        )
        stderr_path = Path(stderr_file.name)
        stderr_file.close()

        stderr_fd = open(stderr_path, "w")

        # 在后台启动进程
        # 使用 Popen 启动，分离进程组，避免父进程退出时子进程也被终止
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": stderr_fd,
            "start_new_session": True,  # 创建新的进程组
        }
        if project_root:
            popen_kwargs["cwd"] = project_root

        process = subprocess.Popen(cmd, **popen_kwargs)

        # 等待代理启动（mitmproxy 需要几秒时间初始化）
        max_wait = 10  # 最多等待 10 秒
        waited = 0
        started = False

        while waited < max_wait:
            time.sleep(0.5)
            waited += 0.5

            # 检查进程是否还在运行
            if process.poll() is not None:
                # 进程已退出，读取错误信息
                stderr_fd.close()
                error_msg = ""
                try:
                    if stderr_path.exists():
                        error_msg = stderr_path.read_text()
                        stderr_path.unlink()
                except Exception:
                    pass

                return with_setup_proxy_guard({
                    "success": False,
                    "message": f"代理启动失败（退出码: {process.returncode}）。"
                    + (f"\n错误信息: {error_msg[:500]}" if error_msg else ""),
                })

            # 检查端口是否开始监听
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                if result == 0:
                    # 端口已开始监听，代理启动成功
                    started = True
                    break
            except Exception:
                pass

        # 关闭错误输出文件
        try:
            stderr_fd.close()
            if started:
                # 启动成功，删除日志文件
                stderr_path.unlink(missing_ok=True)
        except Exception:
            pass

        # 最终检查：确认端口在监听
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            if result != 0:
                return with_setup_proxy_guard({
                    "success": False,
                    "message": f"代理进程已启动（PID: {process.pid}），但端口 {port} 未开始监听。"
                    + "请检查代理日志或手动在终端运行 'uv run mitmproxy-start' 查看错误。",
                    "pid": process.pid,
                })
        except Exception:
            pass

        # 保存 PID
        _save_pid(process.pid)

        return with_setup_proxy_guard({
            "success": True,
            "message": f"代理已启动（PID: {process.pid}, 端口: {port}）。"
            + (
                "\n⚠️  注意：Mac 浏览器访问 HTTPS 网站需要安装 CA 证书。"
                + "在浏览器中访问 http://mitm.it 下载并安装证书。"
                if setup_proxy
                else ""
            ),
            "pid": process.pid,
            "port": port,
            "certificate_hint": "Mac 浏览器访问 HTTPS 网站需要安装 CA 证书。访问 http://mitm.it 下载。",
        })
    except Exception as e:
        return with_setup_proxy_guard({
            "success": False,
            "message": f"启动代理时出错: {e}",
        })


def proxy_status() -> dict[str, Any]:
    """
    获取代理状态

    Returns:
        代理状态信息
    """
    if not SQLiteTrafficStore.exists():
        return {
            "running": False,
            "message": "代理未启动。请先运行: uv run mitmproxy-start",
        }

    store = SQLiteTrafficStore()
    return {
        "running": True,
        "message": "代理正在运行（通过 mitmproxy-start 启动）",
        "traffic_count": len(store),
        "db_path": str(SQLiteTrafficStore.get_default_path()),
    }


def proxy_stop(port: int = 8888) -> dict[str, Any]:
    """
    停止代理服务

    Args:
        port: 代理端口，默认 8888（用于查找进程）

    Returns:
        包含停止状态的字典
    """
    # 先尝试从 PID 文件读取
    pid = _read_pid()

    # 如果 PID 文件不存在或进程不存在，尝试通过端口查找
    if not pid:
        pid = _find_proxy_process_by_port(port)

    if not pid:
        return {
            "success": False,
            "message": "未找到运行中的代理进程。",
        }

    try:
        # 先尝试优雅退出（SIGTERM）
        os.kill(pid, 15)  # SIGTERM

        # 等待进程退出（最多等待 3 秒）
        for _ in range(30):  # 30 * 0.1 = 3 秒
            try:
                os.kill(pid, 0)  # 检查进程是否还存在
            except (OSError, ProcessLookupError):
                # 进程已退出
                PID_FILE.unlink(missing_ok=True)
                return {
                    "success": True,
                    "message": f"代理已停止（PID: {pid}）",
                    "pid": pid,
                }
            time.sleep(0.1)

        # 如果优雅退出失败，强制终止（SIGKILL）
        os.kill(pid, 9)  # SIGKILL
        time.sleep(0.5)

        # 再次检查
        try:
            os.kill(pid, 0)
            return {
                "success": False,
                "message": f"无法停止代理进程（PID: {pid}）。请手动终止。",
                "pid": pid,
            }
        except (OSError, ProcessLookupError):
            PID_FILE.unlink(missing_ok=True)
            return {
                "success": True,
                "message": f"代理已强制停止（PID: {pid}）",
                "pid": pid,
            }
    except (OSError, ProcessLookupError) as e:
        # 进程不存在
        PID_FILE.unlink(missing_ok=True)
        return {
            "success": False,
            "message": f"进程不存在或无法访问（PID: {pid}）: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"停止代理时出错: {e}",
        }
