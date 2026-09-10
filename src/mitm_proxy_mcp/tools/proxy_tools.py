"""
代理工具

提供证书信息查询、代理启动/停止功能。
"""

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import IO, Any, NamedTuple

from ..android.cert_injector import CertHelper
from ..control.capture_context import resolve_setup_proxy
from ..control.runtime import is_pid_alive, read_runtime, reap
from ..core.sqlite_store import SQLiteTrafficStore

# PID 文件路径（用于跟踪代理进程）
PID_FILE = Path("/tmp/mitmproxy-mcp.pid")
TERMINAL_SCRIPT_PATH = Path("/tmp/mitmproxy-mcp-start.command")
DEFAULT_UI_URL = "http://127.0.0.1:18765"


class ProxyLaunch(NamedTuple):
    process: subprocess.Popen[bytes] | None
    stderr_path: Path | None
    stderr_fd: IO[str] | None
    in_terminal: bool


def _posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _shell_command(cmd: list[str], cwd: Path | None) -> str:
    shell = " ".join(_posix_quote(part) for part in cmd)
    if cwd is not None:
        shell = f"cd {_posix_quote(str(cwd))} && {shell}"
    return shell


def build_terminal_command_script(cmd: list[str], cwd: Path | None) -> str:
    """生成在 Terminal.app 里执行的 .command 脚本。"""
    lines = ["#!/bin/bash", "set -e"]
    if cwd is not None:
        lines.append(f"cd {_posix_quote(str(cwd))}")
    lines.append("exec " + " ".join(_posix_quote(part) for part in cmd))
    return "\n".join(lines) + "\n"


def build_terminal_do_script(cmd: list[str], cwd: Path | None) -> str:
    """AppleScript：激活 Terminal 并执行启动命令。"""
    return (
        'tell application "Terminal"\n'
        "activate\n"
        f"do script {_applescript_quote(_shell_command(cmd, cwd))}\n"
        "end tell\n"
    )


def _launch_in_mac_terminal(cmd: list[str], cwd: Path | None) -> None:
    result = subprocess.run(
        ["osascript"],
        input=build_terminal_do_script(cmd, cwd),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0:
        return

    TERMINAL_SCRIPT_PATH.write_text(build_terminal_command_script(cmd, cwd))
    TERMINAL_SCRIPT_PATH.chmod(0o700)
    opened = subprocess.run(
        ["open", "-a", "Terminal", str(TERMINAL_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if opened.returncode != 0:
        detail = (
            (result.stderr or result.stdout or "").strip()
            or (opened.stderr or opened.stdout or "open Terminal failed").strip()
        )
        raise RuntimeError(detail)


def _popen_detached(cmd: list[str], project_root: Path | None) -> ProxyLaunch:
    """
    Launch the proxy in the background with no usable stdin.

    An inherited stdin left the start wizard blocked forever on its "kill the
    process holding the port?" input() — the launch reported a timeout while the
    process stayed alive as a stray. With DEVNULL that input() raises EOFError,
    which the wizard already handles by exiting.

    后台启动代理，并且不给它可用的 stdin。
    继承 stdin 会让启动向导永远卡在「是否关闭占用端口的进程？」的 input() 上——
    启动这边报超时，进程却还活着变成残留。改成 DEVNULL 后 input() 抛 EOFError，
    向导本来就有对应的退出分支。
    """
    import tempfile

    stderr_file = tempfile.NamedTemporaryFile(
        mode="w+", delete=False, suffix=".log", prefix="mitmproxy-"
    )
    stderr_path = Path(stderr_file.name)
    stderr_file.close()
    stderr_fd = open(stderr_path, "w")

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_fd,
        "start_new_session": True,
    }
    if project_root:
        popen_kwargs["cwd"] = project_root

    process = subprocess.Popen(cmd, **popen_kwargs)
    return ProxyLaunch(process, stderr_path, stderr_fd, False)


def _launch_proxy(cmd: list[str], project_root: Path | None) -> ProxyLaunch:
    """后台启动 mitmproxy-start，不另开 Terminal，也不改 Mac 系统代理。"""
    return _popen_detached(cmd, project_root)


def control_ui_url() -> str:
    """本机控制台地址，不含 token。"""
    try:
        info = read_runtime()
        if info is not None and info.base_url:
            return info.base_url.rstrip("/")
    except Exception:
        pass
    return DEFAULT_UI_URL


def open_control_ui() -> str:
    """打开控制台网页，返回实际打开的 URL。"""
    url = control_ui_url()
    webbrowser.open(url)
    return url


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
    """
    PID from the pid file, or None when that process is gone.

    A zombie is treated as gone and reaped here: signal 0 succeeds against one,
    so the stale pid otherwise made proxy_start refuse to start and proxy_stop
    report that it could not kill anything.

    读取 PID 文件里的进程号，进程已消失时返回 None。
    僵尸按「已消失」处理并就地回收：signal 0 对僵尸是成功的，否则这个陈旧 pid
    会让 proxy_start 拒绝启动、proxy_stop 报「无法停止」。
    """
    try:
        if not PID_FILE.exists():
            return None
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        return None

    if is_pid_alive(pid):
        return pid

    reap(pid)
    PID_FILE.unlink(missing_ok=True)
    return None


def _wait_for_port_release(port: int, timeout: float = 3.0) -> bool:
    """
    Block until nothing holds the proxy port, so a stop is safe to follow with a start.

    mitmdump exits a couple of hundred milliseconds after the wrapper script it
    runs under, so returning as soon as the tracked pid is gone left the port
    still taken and made an immediate restart fail.

    等到没有进程占着代理端口再返回，好让 stop 之后可以立刻 start。
    mitmdump 比拉起它的启动脚本晚几百毫秒退出，一旦追踪的 pid 消失就返回，
    端口还占着，紧接着的重启就会失败。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _find_proxy_process_by_port(port) is None:
            return True
        time.sleep(0.1)
    return _find_proxy_process_by_port(port) is None


def _signal_process_tree(pid: int, sig: int) -> None:
    """
    Signal the whole process group when the pid leads one.

    The proxy is launched with start_new_session=True, so the wrapper script and
    the mitmdump it spawns share a group. Signalling only the wrapper orphaned
    mitmdump, which kept holding port 8888 and blocked the next start. The
    pgid == pid guard keeps this from ever hitting an unrelated group.

    当 pid 是进程组组长时，对整个进程组发信号。
    代理以 start_new_session=True 启动，启动脚本和它拉起的 mitmdump 同属一个
    进程组。只对脚本发信号会让 mitmdump 变成孤儿继续占着 8888 端口，导致下次
    启动失败。pgid == pid 的判断保证不会误伤无关进程组。
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None

    if pgid is not None and pgid == pid:
        try:
            os.killpg(pgid, sig)
            return
        except OSError:
            pass

    try:
        os.kill(pid, sig)
    except (OSError, ProcessLookupError):
        pass


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


def proxy_start(port: int = 8888, setup_proxy: bool = False, open_ui: bool = False) -> dict[str, Any]:
    """
    后台启动代理服务，不另开终端窗口。

    Args:
        port: 代理端口，默认 8888
        setup_proxy: 是否自动设置 Mac Wi-Fi 系统代理，默认 False（不覆盖本机代理）
        open_ui: 代理就绪后是否打开本机控制台网页，默认 False（网页「启动」会传 True）

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
        launch = _launch_proxy(cmd, project_root)
        process = launch.process
        stderr_fd = launch.stderr_fd
        stderr_path = launch.stderr_path

        # 等待代理开始监听
        max_wait = 10
        waited = 0
        started = False

        while waited < max_wait:
            time.sleep(0.5)
            waited += 0.5

            if process is not None and process.poll() is not None:
                if stderr_fd is not None:
                    stderr_fd.close()
                error_msg = ""
                try:
                    if stderr_path is not None and stderr_path.exists():
                        error_msg = stderr_path.read_text()
                        stderr_path.unlink()
                except Exception:
                    pass

                return with_setup_proxy_guard({
                    "success": False,
                    "message": f"代理启动失败（退出码: {process.returncode}）。"
                    + (f"\n错误信息: {error_msg[:500]}" if error_msg else ""),
                })

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                if result == 0:
                    started = True
                    break
            except Exception:
                pass

        if stderr_fd is not None:
            try:
                stderr_fd.close()
                if started and stderr_path is not None:
                    stderr_path.unlink(missing_ok=True)
            except Exception:
                pass

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            if result != 0:
                pid = process.pid if process is not None else None
                message = (
                    f"代理进程已启动（PID: {pid}），但端口 {port} 未开始监听。"
                    "请检查代理日志或手动运行 'uv run mitmproxy-start' 查看错误。"
                )
                payload: dict[str, Any] = {"success": False, "message": message}
                if pid is not None:
                    payload["pid"] = pid
                return with_setup_proxy_guard(payload)
        except Exception:
            pass

        pid = process.pid if process is not None else _find_proxy_process_by_port(port)
        if pid:
            _save_pid(pid)

        started_msg = f"代理已启动（PID: {pid}, 端口: {port}）。"
        ui_url = None
        if open_ui:
            try:
                ui_url = open_control_ui()
            except Exception:
                ui_url = control_ui_url()
            started_msg += f"\n控制台: {ui_url}/"
        return with_setup_proxy_guard({
            "success": True,
            "message": started_msg
            + (
                "\n⚠️  注意：Mac 浏览器访问 HTTPS 网站需要安装 CA 证书。"
                + "在浏览器中访问 http://mitm.it 下载并安装证书。"
                if setup_proxy
                else ""
            ),
            "pid": pid,
            "port": port,
            "in_terminal": launch.in_terminal,
            "ui_url": ui_url,
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

    def _stopped() -> dict[str, Any]:
        # 回收后再删 PID 文件，否则下次判活会撞上自己留下的僵尸。
        reap(pid)
        PID_FILE.unlink(missing_ok=True)
        _wait_for_port_release(port)
        return {
            "success": True,
            "message": f"代理已停止（PID: {pid}）",
            "pid": pid,
        }

    try:
        # 先尝试优雅退出（SIGTERM），按进程组发以带走 mitmdump
        _signal_process_tree(pid, 15)

        # 等待进程退出（最多等待 3 秒）
        for _ in range(30):  # 30 * 0.1 = 3 秒
            reap(pid)
            if not is_pid_alive(pid):
                return _stopped()
            time.sleep(0.1)

        # 如果优雅退出失败，强制终止（SIGKILL）
        _signal_process_tree(pid, 9)
        time.sleep(0.5)
        reap(pid)

        # 再次检查
        if is_pid_alive(pid):
            return {
                "success": False,
                "message": f"无法停止代理进程（PID: {pid}）。请手动终止。",
                "pid": pid,
            }
        else:
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
