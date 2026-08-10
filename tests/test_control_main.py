"""控制服务进程入口测试。"""

import os
from unittest.mock import Mock

from mitm_proxy_mcp.control.runtime import RuntimeInfo


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
