"""proxy_start 后台启动代理，不另开 Terminal，默认不改 Mac 系统代理。"""

from pathlib import Path

from mitm_proxy_mcp.tools import proxy_tools
from mitm_proxy_mcp.tools.proxy_tools import (
    ProxyLaunch,
    build_terminal_command_script,
    build_terminal_do_script,
)


def test_build_terminal_command_script_quotes_cwd_and_args():
    script = build_terminal_command_script(
        ["/usr/bin/uv", "run", "mitmproxy-start", "--port", "8888"],
        Path("/tmp/it's project"),
    )
    assert script.startswith("#!/bin/bash\n")
    assert "cd '/tmp/it'\\''s project'" in script
    assert "exec '/usr/bin/uv' 'run' 'mitmproxy-start' '--port' '8888'" in script


def test_build_terminal_do_script_quotes_for_applescript():
    script = build_terminal_do_script(
        ["/usr/bin/uv", "run", "mitmproxy-start"],
        Path('/tmp/proj "x"'),
    )
    assert script == (
        'tell application "Terminal"\n'
        "activate\n"
        'do script "cd \'/tmp/proj \\"x\\"\' && \'/usr/bin/uv\' \'run\' \'mitmproxy-start\'"\n'
        "end tell\n"
    )


def test_launch_proxy_uses_background_popen(monkeypatch):
    class FakeProc:
        pid = 99

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(proxy_tools.sys, "platform", "darwin")
    monkeypatch.setattr(proxy_tools.subprocess, "Popen", fake_popen)

    launch = proxy_tools._launch_proxy(["uv", "run", "mitmproxy-start"], Path("/tmp/proj"))

    assert captured["cmd"] == ["uv", "run", "mitmproxy-start"]
    assert captured["kwargs"]["cwd"] == Path("/tmp/proj")
    assert launch.in_terminal is False
    assert launch.process is not None
    assert launch.process.pid == 99
    if launch.stderr_fd is not None:
        launch.stderr_fd.close()
    if launch.stderr_path is not None:
        launch.stderr_path.unlink(missing_ok=True)


def test_launch_proxy_does_not_open_mac_terminal(monkeypatch):
    called = {"terminal": False}

    def fake_terminal(cmd, cwd):
        called["terminal"] = True

    class FakeProc:
        pid = 1

    monkeypatch.setattr(proxy_tools, "_launch_in_mac_terminal", fake_terminal)
    monkeypatch.setattr(
        proxy_tools.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProc(),
    )

    launch = proxy_tools._launch_proxy(["uv", "run", "mitmproxy-start"], None)

    assert called["terminal"] is False
    assert launch.in_terminal is False
    if launch.stderr_fd is not None:
        launch.stderr_fd.close()
    if launch.stderr_path is not None:
        launch.stderr_path.unlink(missing_ok=True)


def test_proxy_start_uses_background_process_and_saves_pid(monkeypatch):
    class FreeThenBusy:
        def __init__(self):
            self.calls = 0

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            self.calls += 1
            return 1 if self.calls == 1 else 0

        def close(self):
            pass

    class AliveProc:
        pid = 4242

        def poll(self):
            return None

    sockets = FreeThenBusy()
    opened = {}

    def fake_launch(cmd, project_root=None):
        opened["cmd"] = cmd
        return ProxyLaunch(AliveProc(), None, None, False)

    monkeypatch.setattr(proxy_tools, "_read_pid", lambda: None)
    monkeypatch.setattr(proxy_tools, "_find_proxy_process_by_port", lambda port: None)
    monkeypatch.setattr(proxy_tools, "_save_pid", lambda pid: opened.__setitem__("pid", pid))
    monkeypatch.setattr(proxy_tools, "_launch_proxy", fake_launch)
    monkeypatch.setattr(proxy_tools.socket, "socket", lambda *args: sockets)
    monkeypatch.setattr(proxy_tools.time, "sleep", lambda _: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)

    result = proxy_tools.proxy_start(port=8888, setup_proxy=False)

    assert opened["cmd"][:3] == ["/usr/bin/uv", "run", "mitmproxy-start"]
    assert "--port" in opened["cmd"]
    assert "--setup-proxy" not in opened["cmd"]
    assert result["success"] is True
    assert result["in_terminal"] is False
    assert result["pid"] == 4242
    assert opened["pid"] == 4242
    assert "终端" not in result["message"]


def test_control_ui_url_strips_slash_and_never_uses_token(monkeypatch):
    monkeypatch.setattr(
        proxy_tools,
        "read_runtime",
        lambda: type("R", (), {"base_url": "http://127.0.0.1:19999/"})(),
    )
    assert proxy_tools.control_ui_url() == "http://127.0.0.1:19999"


def test_proxy_start_opens_ui_after_listen(monkeypatch):
    class FreeThenBusy:
        def __init__(self):
            self.calls = 0

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            self.calls += 1
            return 1 if self.calls == 1 else 0

        def close(self):
            pass

    sockets = FreeThenBusy()
    opened = {}
    urls: list[str] = []

    def fake_find(port):
        return 7 if opened.get("ok") else None

    def fake_launch(cmd, project_root=None):
        opened["ok"] = True
        return ProxyLaunch(type("P", (), {"pid": 7, "poll": lambda self: None})(), None, None, False)

    monkeypatch.setattr(proxy_tools.sys, "platform", "darwin")
    monkeypatch.setattr(proxy_tools, "_read_pid", lambda: None)
    monkeypatch.setattr(proxy_tools, "_find_proxy_process_by_port", fake_find)
    monkeypatch.setattr(proxy_tools, "_save_pid", lambda pid: None)
    monkeypatch.setattr(proxy_tools, "_launch_proxy", fake_launch)
    monkeypatch.setattr(proxy_tools.socket, "socket", lambda *args: sockets)
    monkeypatch.setattr(proxy_tools.time, "sleep", lambda _: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    monkeypatch.setattr(proxy_tools, "control_ui_url", lambda: "http://127.0.0.1:18765")
    monkeypatch.setattr(proxy_tools.webbrowser, "open", lambda url: urls.append(url))

    result = proxy_tools.proxy_start(port=8888, setup_proxy=False, open_ui=True)

    assert opened["ok"] is True
    assert result["success"] is True
    assert urls == ["http://127.0.0.1:18765"]
    assert result["ui_url"] == "http://127.0.0.1:18765"
    assert "控制台: http://127.0.0.1:18765/" in result["message"]


def test_proxy_start_skips_browser_by_default(monkeypatch):
    class FreeThenBusy:
        def __init__(self):
            self.calls = 0

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            self.calls += 1
            return 1 if self.calls == 1 else 0

        def close(self):
            pass

    urls: list[str] = []
    sockets = FreeThenBusy()
    launched = {"v": False}

    def fake_find(port):
        return 7 if launched["v"] else None

    def fake_launch(cmd, project_root=None):
        launched["v"] = True
        return ProxyLaunch(type("P", (), {"pid": 7, "poll": lambda self: None})(), None, None, False)

    monkeypatch.setattr(proxy_tools.sys, "platform", "darwin")
    monkeypatch.setattr(proxy_tools, "_read_pid", lambda: None)
    monkeypatch.setattr(proxy_tools, "_find_proxy_process_by_port", fake_find)
    monkeypatch.setattr(proxy_tools, "_save_pid", lambda pid: None)
    monkeypatch.setattr(proxy_tools, "_launch_proxy", fake_launch)
    monkeypatch.setattr(proxy_tools.socket, "socket", lambda *args: sockets)
    monkeypatch.setattr(proxy_tools.time, "sleep", lambda _: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    monkeypatch.setattr(proxy_tools.webbrowser, "open", lambda url: urls.append(url))

    result = proxy_tools.proxy_start(port=8888, setup_proxy=False)

    assert result["success"] is True
    assert urls == []
    assert result.get("ui_url") is None
