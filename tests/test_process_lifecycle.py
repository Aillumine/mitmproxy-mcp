"""代理进程判活与停止：僵尸不算活着，停止要带走整棵进程树。"""

import os
import socket
import subprocess
import sys
import time

import pytest

from mitm_proxy_mcp.control.runtime import is_pid_alive, is_zombie, reap
from mitm_proxy_mcp.tools import proxy_tools


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestZombieAwareLiveness:
    """signal 0 对僵尸是成功的，这正是停掉的代理一直被判为「还在跑」的原因。"""

    def test_zombie_is_not_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        pid = child.pid
        # 不调用 wait()，让它保持未回收状态
        assert _wait_until(lambda: is_zombie(pid)), "子进程未进入僵尸状态"

        # 旧逻辑用的就是这一句，它对僵尸返回成功
        os.kill(pid, 0)

        assert is_pid_alive(pid) is False
        child.wait()

    def test_running_process_is_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            assert is_pid_alive(child.pid) is True
            assert is_zombie(child.pid) is False
        finally:
            child.kill()
            child.wait()

    def test_reap_clears_the_zombie(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        pid = child.pid
        assert _wait_until(lambda: is_zombie(pid))

        reap(pid)
        assert _wait_until(lambda: not is_zombie(pid)), "回收后仍是僵尸"
        child.returncode = 0  # Popen 已被我们代收，避免析构时告警

    def test_reap_ignores_a_process_we_do_not_own(self):
        reap(os.getpid())  # 不是自己的子进程，必须静默返回


class TestReadPid:
    def test_stale_zombie_pid_is_cleared(self, tmp_path, monkeypatch):
        """PID 文件指向僵尸时要返回 None 并清掉文件，否则启动/停止双双卡死。"""
        pid_file = tmp_path / "proxy.pid"
        monkeypatch.setattr(proxy_tools, "PID_FILE", pid_file)

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        pid_file.write_text(str(child.pid))
        assert _wait_until(lambda: is_zombie(child.pid))

        assert proxy_tools._read_pid() is None
        assert not pid_file.exists()
        child.returncode = 0

    def test_live_pid_is_returned(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "proxy.pid"
        monkeypatch.setattr(proxy_tools, "PID_FILE", pid_file)

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            pid_file.write_text(str(child.pid))
            assert proxy_tools._read_pid() == child.pid
            assert pid_file.exists()
        finally:
            child.kill()
            child.wait()

    def test_missing_pid_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_tools, "PID_FILE", tmp_path / "absent.pid")
        assert proxy_tools._read_pid() is None


class TestSignalProcessTree:
    """mitmdump 是启动脚本的子进程，只杀父进程会留下孤儿继续占着端口。"""

    def test_signal_reaches_the_child_in_the_group(self, tmp_path):
        marker = tmp_path / "child.pid"
        script = (
            "import os, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", script], start_new_session=True
        )
        try:
            assert _wait_until(lambda: marker.exists() and marker.read_text().strip())
            child_pid = int(marker.read_text().strip())
            assert is_pid_alive(child_pid)

            proxy_tools._signal_process_tree(parent.pid, 15)

            assert _wait_until(lambda: not is_pid_alive(parent.pid)), "父进程未退出"
            assert _wait_until(
                lambda: not is_pid_alive(child_pid)
            ), "子进程成了孤儿——只杀父进程不够"
        finally:
            parent.kill()
            parent.wait()

    def test_falls_back_to_single_pid_when_not_group_leader(self):
        """非组长进程只对它自己发信号，绝不能波及无关进程组。"""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            assert os.getpgid(child.pid) != child.pid
            proxy_tools._signal_process_tree(child.pid, 15)
            assert _wait_until(lambda: not is_pid_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


class TestProxyStop:
    def test_stop_reaps_and_clears_pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "proxy.pid"
        monkeypatch.setattr(proxy_tools, "PID_FILE", pid_file)

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid_file.write_text(str(child.pid))

        result = proxy_tools.proxy_stop()

        assert result["success"] is True
        assert result["pid"] == child.pid
        assert not pid_file.exists()
        assert not is_zombie(child.pid), "停止后不应留下僵尸"
        child.returncode = 0

    def test_stop_reports_when_nothing_is_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_tools, "PID_FILE", tmp_path / "absent.pid")
        monkeypatch.setattr(proxy_tools, "_find_proxy_process_by_port", lambda port: None)

        result = proxy_tools.proxy_stop()
        assert result["success"] is False
        assert "未找到" in result["message"]


class TestPortAvailability:
    """停止后紧接着重启，被残留连接误判为端口占用。"""

    def test_lingering_connection_does_not_look_like_a_busy_port(self):
        """
        A closed-but-lingering connection blocks a plain bind while mitmproxy,
        which sets SO_REUSEADDR, would still start.

        已关闭但残留的连接会让不带 SO_REUSEADDR 的 bind 失败，
        而设置了该选项的 mitmproxy 其实能正常启动。
        """
        from mitm_proxy_mcp.cli.start import check_port_available

        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)

        client = socket.create_connection(("127.0.0.1", port))
        served, _ = listener.accept()

        # 留下一条处于关闭中的连接，再放掉监听套接字
        client.close()
        listener.close()

        try:
            assert check_port_available(port) is True
        finally:
            served.close()

    def test_actively_listening_port_is_reported_busy(self):
        from mitm_proxy_mcp.cli.start import check_port_available

        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        try:
            assert check_port_available(port) is False
        finally:
            listener.close()


class TestWaitForPortRelease:
    """mitmdump 比启动脚本晚退出，stop 提前返回会让紧接着的 start 撞上占用。"""

    def test_returns_once_the_port_is_free(self, monkeypatch):
        seen = iter([1234, 1234, None])
        monkeypatch.setattr(
            proxy_tools, "_find_proxy_process_by_port", lambda port: next(seen)
        )
        assert proxy_tools._wait_for_port_release(8888, timeout=2.0) is True

    def test_gives_up_after_the_timeout(self, monkeypatch):
        monkeypatch.setattr(
            proxy_tools, "_find_proxy_process_by_port", lambda port: 1234
        )
        started = time.monotonic()
        assert proxy_tools._wait_for_port_release(8888, timeout=0.3) is False
        assert time.monotonic() - started < 2.0

    def test_stop_does_not_return_while_the_port_is_held(self, tmp_path, monkeypatch):
        """真实进程：stop 返回时端口必须已经空出来。"""
        pid_file = tmp_path / "proxy.pid"
        monkeypatch.setattr(proxy_tools, "PID_FILE", pid_file)

        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)

        # 启动脚本先退出，占端口的「mitmdump」稍后才退出
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.4)"],
            start_new_session=True,
        )
        pid_file.write_text(str(holder.pid))

        release_at = time.monotonic() + 0.4
        monkeypatch.setattr(
            proxy_tools,
            "_find_proxy_process_by_port",
            lambda p: None if time.monotonic() > release_at else 4242,
        )
        try:
            result = proxy_tools.proxy_stop(port=port)
            assert result["success"] is True
            assert proxy_tools._find_proxy_process_by_port(port) is None
        finally:
            listener.close()
            if holder.poll() is None:
                holder.kill()
            holder.wait()
