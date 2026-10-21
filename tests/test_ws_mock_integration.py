import json
import os
import socket
import ssl
import subprocess
import sys
import time
from collections import deque
from datetime import UTC
from pathlib import Path

import pytest
import wsproto
from wsproto import events

from mitm_proxy_mcp.core.mock_store import MockRule, MockStore


class Client:
    def __init__(self, port, path="/ws", *, secure=False, host="offline.invalid"):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.socket.settimeout(5)
        if secure:
            from urllib.parse import urlsplit

            parsed = urlsplit("//" + host)
            address = host if parsed.port else f"{host}:443"
            self.socket.sendall(
                f"CONNECT {address} HTTP/1.1\r\nHost: {address}\r\n\r\n".encode()
            )
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += self.socket.recv(1)
            assert b"200" in header
            self.socket = ssl._create_unverified_context().wrap_socket(
                self.socket, server_hostname=parsed.hostname
            )
        self.ws = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
        self.pending = deque()
        target = path if secure else f"http://{host}{path}"
        self.socket.sendall(self.ws.send(events.Request(host=host, target=target)))
        accepted = self.receive()
        assert isinstance(accepted, events.AcceptConnection), accepted

    def send(self, text):
        self.socket.sendall(self.ws.send(events.TextMessage(data=text)))

    def receive(self):
        while not self.pending:
            data = self.socket.recv(65536)
            assert data, "connection closed unexpectedly"
            self.ws.receive_data(data)
            self.pending.extend(self.ws.events())
        return self.pending.popleft()

    def text(self):
        event = self.receive()
        assert isinstance(event, events.TextMessage), event
        return event.data

    def close(self):
        self.socket.close()


@pytest.fixture
def proxy(tmp_path):
    store = MockStore(tmp_path / "mock.db")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = dict(
        os.environ,
        MITMPROXY_DB_PATH=str(tmp_path / "traffic.db"),
        MITMPROXY_MOCK_DB_PATH=str(store.db_path),
        MITMPROXY_THROTTLE_PATH=str(tmp_path / "throttle.json"),
    )
    addon = (
        Path(__file__).resolve().parents[1]
        / "src/mitm_proxy_mcp/addon/traffic_addon.py"
    )
    from mitm_proxy_mcp.cli.start import mitmdump_args

    args = mitmdump_args(port, str(addon))
    args += [
        "--set",
        f"confdir={tmp_path / 'certs'}",
        "--set",
        "termlog_verbosity=error",
    ]
    log = (tmp_path / "proxy.log").open("w+")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from mitmproxy.tools.main import mitmdump; mitmdump()",
            *args,
        ],
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                log.seek(0)
                pytest.fail(log.read())
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail("temporary proxy did not start")
                time.sleep(0.05)
        yield port, store
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.seek(0)
        output = log.read()
        log.close()
        for marker in (
            "Addon error",
            "Task exception was never retrieved",
            "mitmproxy has crashed",
        ):
            assert marker not in output, output


def rule(store, name, mock_type, ws_config, *, url="offline.invalid", body=""):
    store.add(
        MockRule(
            id=name,
            name=name,
            url_pattern=url,
            mock_type=mock_type,
            ws_config=ws_config,
            response_body=body,
        )
    )


@pytest.mark.parametrize("secure", [False, True])
def test_offline_ws_and_wss_push_reply_and_ping(proxy, secure):
    port, store = proxy
    rule(store, "server", "ws_server", {"messages": [{"body": "hello", "delay_ms": 0}]})
    rule(
        store,
        "reply",
        "ws_message",
        {
            "direction": "client",
            "action": "reply",
            "message_pattern": "request",
            "message_match_type": "exact",
        },
        body="response",
    )
    client = Client(port, secure=secure)
    try:
        assert client.text() == "hello"
        client.send("request")
        assert client.text() == "response"
        client.socket.sendall(client.ws.send(events.Ping(payload=b"probe")))
        assert isinstance(client.receive(), events.Pong)
        assert store.get_by_id("server").hit_count == 1
        assert store.get_by_id("reply").hit_count == 1
        import sqlite3

        with sqlite3.connect(store.db_path.parent / "traffic.db") as conn:
            rows = conn.execute("SELECT url, response_headers FROM traffic").fetchall()
        assert rows
        assert all("127.0.0.1" not in url for url, _ in rows)
        assert any(
            json.loads(headers).get("X-Mock-Rule") == "reply" for _, headers in rows
        )
    finally:
        client.close()


def test_socketio_connect_event_reply_and_heartbeat(proxy):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {
            "protocol": "socketio",
            "ping_interval_ms": 200,
            "ping_timeout_ms": 300,
            "messages": [{"body": '42["ready"]'}],
        },
    )
    rule(
        store,
        "reply",
        "ws_message",
        {
            "direction": "client",
            "action": "reply",
            "message_pattern": '42["ask"]',
            "message_match_type": "exact",
        },
        body='42["answer"]',
    )
    client = Client(port, "/socket.io/?EIO=4&transport=websocket", secure=True)
    try:
        opened = client.text()
        assert opened.startswith("0")
        assert json.loads(opened[1:])["upgrades"] == []
        client.send("40")
        assert client.text().startswith('40{"sid":')
        assert client.text() == '42["ready"]'
        client.send('42["ask"]')
        assert client.text() == '42["answer"]'
        assert client.text() == "2"
        client.send("3")
        assert client.text() == "2"
        closed = client.receive()
        assert isinstance(closed, events.CloseConnection)
        assert closed.reason == "ping timeout"
    finally:
        client.close()


def test_message_replace_and_drop_on_offline_pushes(proxy):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {"messages": [{"body": "drop-me"}, {"body": "original"}]},
    )
    rule(
        store,
        "replace",
        "ws_message",
        {"message_pattern": "original", "message_match_type": "exact"},
        body="replaced",
    )
    rule(store, "drop", "ws_message", {"action": "drop", "message_pattern": "drop-me"})
    client = Client(port)
    try:
        assert client.text() == "replaced"
        assert store.get_by_id("replace").hit_count == 1
        assert store.get_by_id("drop").hit_count == 1
    finally:
        client.close()


@pytest.fixture
def upstream(request, tmp_path):
    import socketserver
    import threading

    received = []
    secure = getattr(request, "param", False)
    tls = None
    if secure:
        from datetime import datetime, timedelta

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "origin.pem"
        key_path = tmp_path / "origin.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_path, key_path)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            ws = wsproto.WSConnection(wsproto.ConnectionType.SERVER)
            try:
                while data := self.request.recv(65536):
                    ws.receive_data(data)
                    for event in ws.events():
                        if isinstance(event, events.Request):
                            self.request.sendall(ws.send(events.AcceptConnection()))
                            self.request.sendall(
                                ws.send(events.TextMessage(data="original"))
                            )
                        elif isinstance(
                            event, (events.TextMessage, events.BytesMessage)
                        ):
                            received.append(event.data)
                            self.request.sendall(ws.send(event))
                        elif isinstance(event, events.CloseConnection):
                            return
            except ConnectionError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

        def get_request(self):
            sock, address = super().get_request()
            return (tls.wrap_socket(sock, server_side=True) if tls else sock), address

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], received, secure
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_live_upstream_replace_reply_binary_and_toggle(proxy, upstream):
    port, store = proxy
    upstream_port, received, _ = upstream
    host = f"127.0.0.1:{upstream_port}"
    rule(
        store,
        "replace",
        "ws_message",
        {"message_pattern": "original", "message_match_type": "exact"},
        url=f"ws://{host}/ws",
        body="modified",
    )
    rule(
        store,
        "local-reply",
        "ws_message",
        {
            "direction": "client",
            "action": "reply",
            "message_pattern": "local-only",
            "message_match_type": "exact",
        },
        url=f"ws://{host}/ws",
        body="local-response",
    )
    store.add(
        MockRule(
            id="http",
            name="http",
            url_pattern=host,
            response_body="must not intercept upgrade",
        )
    )
    client = Client(port, host=host)
    try:
        assert client.text() == "modified"
        client.send("local-only")
        assert client.text() == "local-response"
        client.send("unchanged")
        assert client.text() == "unchanged"
        assert received == ["unchanged"]
        client.socket.sendall(
            client.ws.send(events.BytesMessage(data=b"\x00\xffbinary"))
        )
        event = client.receive()
        assert isinstance(event, events.BytesMessage)
        assert event.data == b"\x00\xffbinary"
        store.update("replace", enabled=False)
        client.send("original")
        assert client.text() == "original"
    finally:
        client.close()


def test_http_mock_compatibility(proxy):
    port, store = proxy
    store.add(
        MockRule(
            id="http",
            name="http",
            url_pattern="offline.invalid",
            response_body="http-mocked",
        )
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(
            b"GET http://offline.invalid/api HTTP/1.1\r\n"
            b"Host: offline.invalid\r\nConnection: close\r\n\r\n"
        )
        response = b""
        while data := client.recv(65536):
            response += data
        assert b"200 OK" in response
        assert b"http-mocked" in response
        assert b"X-Mock-Rule: http" in response


@pytest.mark.parametrize("secure", [False, True])
def test_fault_delay_preserves_payload_and_order(proxy, secure):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {"messages": [{"body": x} for x in ("one", "two", "three")]},
    )
    rule(store, "delay", "ws_fault", {"action": "delay", "after_messages": 1})
    store.update("delay", delay_ms=180)
    client = Client(port, secure=secure)
    try:
        assert client.text() == "one"
        started = time.monotonic()
        assert client.text() == "two"
        assert time.monotonic() - started >= 0.12
        assert client.text() == "three"
        assert store.get_by_id("delay").hit_count == 2
    finally:
        client.close()


@pytest.mark.parametrize("action", ["close", "abort"])
@pytest.mark.parametrize("secure", [False, True])
def test_fault_timer_closes_idle_connections(proxy, action, secure):
    port, store = proxy
    rule(store, "server", "ws_server", {})
    rule(
        store,
        "fault",
        "ws_fault",
        {
            "action": action,
            "trigger": "time",
            "after_ms": 150,
            "close_code": 4001,
            "close_reason": "test fault",
        },
    )
    client = Client(port, secure=secure)
    try:
        if action == "close":
            event = client.receive()
            assert isinstance(event, events.CloseConnection)
            assert event.code == 4001
            assert event.reason == "test fault"
        else:
            assert client.socket.recv(65536) == b""
        assert store.get_by_id("fault").hit_count == 1
    finally:
        client.close()


def test_fault_close_after_exact_message_count(proxy):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {"messages": [{"body": x} for x in ("one", "two", "three")]},
    )
    rule(store, "fault", "ws_fault", {"action": "close", "after_messages": 2})
    client = Client(port, secure=True)
    try:
        assert client.text() == "one"
        assert client.text() == "two"
        assert isinstance(client.receive(), events.CloseConnection)
    finally:
        client.close()


@pytest.mark.parametrize("preserve", [True, False])
def test_fault_pause_can_preserve_or_break_socketio_heartbeat(proxy, preserve):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {
            "protocol": "socketio",
            "ping_interval_ms": 40,
            "ping_timeout_ms": 100,
            "messages": [{"body": '42["data"]', "delay_ms": 160}],
        },
    )
    rule(
        store,
        "pause",
        "ws_fault",
        {
            "action": "pause",
            "trigger": "time",
            "after_ms": 120,
            "duration_ms": 280,
            "preserve_heartbeat": preserve,
        },
    )
    client = Client(port, "/socket.io/?EIO=4&transport=websocket", secure=True)
    try:
        assert client.text().startswith("0")
        client.send("40")
        assert client.text().startswith("40")
        started = time.monotonic()
        pings = 0
        while True:
            event = client.receive()
            if isinstance(event, events.CloseConnection):
                assert not preserve
                assert event.reason == "ping timeout"
                break
            assert isinstance(event, events.TextMessage)
            if event.data == "2":
                pings += 1
                client.send("3")
            else:
                assert preserve
                assert event.data == '42["data"]'
                assert time.monotonic() - started >= 0.32
                assert pings >= 3
                break
    finally:
        client.close()


@pytest.mark.parametrize("action", ["pause", "close", "abort"])
@pytest.mark.parametrize("upstream", [False, True], indirect=True)
def test_faults_apply_to_live_upstream(proxy, upstream, action):
    port, store = proxy
    upstream_port, _, secure = upstream
    host = f"127.0.0.1:{upstream_port}"
    config = {
        "action": action,
        "trigger": "time",
        "after_ms": 60,
        "duration_ms": 150,
        "close_code": 4002,
        "close_reason": "live fault",
    }
    scheme = "wss" if secure else "ws"
    rule(store, "fault", "ws_fault", config, url=f"{scheme}://{host}/ws")
    client = Client(port, host=host, secure=secure)
    try:
        assert client.text() == "original"
        if action == "pause":
            time.sleep(0.1)
            client.send("echo-after-pause")
            started = time.monotonic()
            assert client.text() == "echo-after-pause"
            assert time.monotonic() - started >= 0.06
            client.send("resumed")
            assert client.text() == "resumed"
        elif action == "close":
            event = client.receive()
            assert isinstance(event, events.CloseConnection)
            assert event.code == 4002
        else:
            assert client.socket.recv(65536) == b""
    finally:
        client.close()


def test_fault_delay_then_counted_close_drains_first_messages(proxy):
    port, store = proxy
    rule(
        store,
        "server",
        "ws_server",
        {"messages": [{"body": x} for x in ("one", "two", "three")]},
    )
    rule(store, "delay", "ws_fault", {"action": "delay"})
    store.update("delay", delay_ms=100)
    rule(store, "close", "ws_fault", {"action": "close", "after_messages": 2})
    for _ in range(2):
        client = Client(port, secure=True)
        try:
            assert client.text() == "one"
            assert client.text() == "two"
            assert isinstance(client.receive(), events.CloseConnection)
        finally:
            client.close()
    assert store.get_by_id("close").hit_count == 2


@pytest.mark.parametrize("action", ["close", "abort"])
@pytest.mark.parametrize("secure", [False, True])
def test_fault_zero_count_closes_after_successful_upgrade(proxy, action, secure):
    port, store = proxy
    rule(store, "server", "ws_server", {})
    rule(store, "fault", "ws_fault", {"action": action, "after_messages": 0})
    client = Client(port, secure=secure)
    try:
        if action == "close":
            assert isinstance(client.receive(), events.CloseConnection)
        else:
            assert client.socket.recv(65536) == b""
    finally:
        client.close()


@pytest.mark.parametrize("upstream", [False, True], indirect=True)
def test_fault_binary_messages_survive_both_direction_delay(proxy, upstream):
    port, store = proxy
    origin_port, _, secure = upstream
    host = f"127.0.0.1:{origin_port}"
    scheme = "wss" if secure else "ws"
    rule(
        store,
        "fault",
        "ws_fault",
        {"action": "delay", "direction": "both"},
        url=f"{scheme}://{host}/ws",
    )
    store.update("fault", delay_ms=70)
    client = Client(port, host=host, secure=secure)
    try:
        assert client.text() == "original"
        started = time.monotonic()
        client.socket.sendall(
            client.ws.send(events.BytesMessage(data=b"\x00\xffpayload"))
        )
        event = client.receive()
        assert isinstance(event, events.BytesMessage)
        assert event.data == b"\x00\xffpayload"
        assert time.monotonic() - started >= 0.10
    finally:
        client.close()
