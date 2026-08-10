"""控制服务进程入口测试。"""

import os
from unittest.mock import Mock

from mitm_proxy_mcp.control.runtime import RuntimeInfo, read_runtime, write_runtime


def _runtime(pid: int, proxy_port: int = 9999) -> RuntimeInfo:
    return RuntimeInfo(
        version=1,
        pid=pid,
        base_url="http://127.0.0.1:18765",
        token="secret",
        traffic_db="/tmp/traffic.db",
        mock_db="/tmp/mock.db",
        proxy_port=proxy_port,
        capture_target="mac",
        started_at="2026-08-06T00:00:00Z",
    )


def test_select_port_falls_back_to_an_ephemeral_local_port(monkeypatch):
    """首选端口不可用时选择一个动态本地端口。"""
    from mitm_proxy_mcp.control import main

    class BusySocket:
        def bind(self, address):
            if address[1] == 18765:
                raise OSError("busy")

        def getsockname(self):
            return ("127.0.0.1", 43210)

        def close(self):
            pass

    monkeypatch.setattr(main.socket, "socket", lambda *_: BusySocket())

    assert main.select_port() == 43210


def test_main_writes_runtime_and_configures_databases(monkeypatch, tmp_path):
    """入口初始化数据路径、写入运行时信息并启动 Uvicorn。"""
    from mitm_proxy_mcp.control import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(main, "select_port", lambda: 18765)
    monkeypatch.setattr(main, "generate_token", lambda: "generated-token")
    monkeypatch.setattr(main, "register_shutdown_handlers", lambda **kwargs: None)
    monkeypatch.setattr(main, "proxy_stop", Mock(return_value={"success": True}))
    written: list[RuntimeInfo] = []
    monkeypatch.setattr(main, "write_runtime", written.append)
    run = Mock()
    monkeypatch.setattr(main.uvicorn, "run", run)

    main.main()

    assert len(written) == 1
    runtime = written[0]
    assert runtime.base_url == "http://127.0.0.1:18765"
    assert runtime.token == "generated-token"
    assert os.environ["MITMPROXY_DB_PATH"] == str(tmp_path / ".mitmscope/traffic.db")
    assert os.environ["MITMPROXY_MOCK_DB_PATH"] == str(tmp_path / ".mitmscope/mock.db")
    run.assert_called_once()


def test_cleanup_stops_the_proxy_port_recorded_in_runtime(monkeypatch, tmp_path):
    """清理使用 runtime.json 中记录的代理端口，而不是硬编码的 8888。"""
    from mitm_proxy_mcp.control import main

    monkeypatch.setenv("HOME", str(tmp_path))
    write_runtime(_runtime(pid=os.getpid(), proxy_port=9999))
    stop = Mock(return_value={"success": True})
    monkeypatch.setattr(main, "proxy_stop", stop)

    main.cleanup()

    stop.assert_called_once_with(port=9999)
    assert read_runtime() is None


def test_cleanup_leaves_another_processs_runtime_alone(monkeypatch, tmp_path):
    """runtime.json 属于其他控制服务时，不删除文件也不停它的代理。"""
    from mitm_proxy_mcp.control import main

    monkeypatch.setenv("HOME", str(tmp_path))
    write_runtime(_runtime(pid=os.getpid() + 1))
    stop = Mock(return_value={"success": True})
    monkeypatch.setattr(main, "proxy_stop", stop)

    main.cleanup()

    stop.assert_not_called()
    assert read_runtime() is not None


def test_cleanup_without_runtime_file_is_a_noop(monkeypatch, tmp_path):
    """没有 runtime.json 时清理不会去动任何代理。"""
    from mitm_proxy_mcp.control import main

    monkeypatch.setenv("HOME", str(tmp_path))
    stop = Mock(return_value={"success": True})
    monkeypatch.setattr(main, "proxy_stop", stop)

    main.cleanup()

    stop.assert_not_called()
