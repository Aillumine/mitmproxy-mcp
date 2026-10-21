import asyncio
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import wsproto
from wsproto import events


class MockWebSocketServer:
    """Keep offline connections on loopback so mitmproxy owns normal WS framing.

    离线连接仅转到回环服务，让 mitmproxy 继续负责正常的 WebSocket 帧处理。
    """

    def __init__(self):
        self.server = None
        self.lock = asyncio.Lock()
        self.pending = {}
        self.tasks = set()

    async def redirect(self, flow, rule):
        async with self.lock:
            if self.server is None:
                self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        token = secrets.token_urlsafe(24)
        self.pending[token] = rule
        flow.metadata["ws_mock_original_url"] = flow.request.url
        flow.metadata["ws_mock_original_host"] = flow.request.headers.get("host")
        flow.metadata["ws_mock_token"] = token
        flow.metadata["ws_mock_rule"] = rule["id"]
        port = self.server.sockets[0].getsockname()[1]
        flow.request.url = f"http://127.0.0.1:{port}/{token}"

    def restore(self, flow):
        metadata = getattr(flow, "metadata", {})
        if not isinstance(metadata, dict):
            return
        original = metadata.pop("ws_mock_original_url", None)
        if original is not None:
            flow.request.url = original
            host = metadata.pop("ws_mock_original_host", None)
            if host is not None:
                flow.request.headers["host"] = host

    def discard(self, flow):
        metadata = getattr(flow, "metadata", {})
        token = (
            metadata.pop("ws_mock_token", None) if isinstance(metadata, dict) else None
        )
        self.pending.pop(token, None)
        self.restore(flow)

    def close(self):
        if self.server is not None:
            self.server.close()
        for task in self.tasks:
            task.cancel()
        self.pending.clear()

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        ws = wsproto.WSConnection(wsproto.ConnectionType.SERVER)
        workers = []
        connected = asyncio.Event()
        pong = asyncio.Event()
        sid = secrets.token_hex(12)
        config = {}

        async def send(event):
            writer.write(ws.send(event))
            await writer.drain()

        async def pushes():
            await connected.wait()
            for item in config.get("messages", []):
                await asyncio.sleep(item.get("delay_ms", 0) / 1000)
                await send(events.TextMessage(data=item["body"]))

        async def heartbeat():
            # Engine.IO v4 servers initiate ping and close peers that miss pong.
            #
            # Engine.IO v4 由服务端发起心跳，未按时 pong 时关闭连接。
            while True:
                await asyncio.sleep(config.get("ping_interval_ms", 25000) / 1000)
                pong.clear()
                await send(events.TextMessage(data="2"))
                try:
                    await asyncio.wait_for(
                        pong.wait(), config.get("ping_timeout_ms", 20000) / 1000
                    )
                except TimeoutError:
                    await send(events.CloseConnection(code=1000, reason="ping timeout"))
                    writer.close()
                    return

        async def run_worker(coro):
            try:
                await coro
            except (ConnectionError, wsproto.utilities.LocalProtocolError):
                writer.close()

        try:
            text = ""
            accepted = False
            while True:
                # Bound incomplete handshakes; established sessions may stay idle.
                #
                # 为未完成握手设置超时，已建立的连接允许持续空闲。
                data = await asyncio.wait_for(
                    reader.read(65536), None if accepted else 10
                )
                if not data:
                    break
                ws.receive_data(data)
                for event in ws.events():
                    if isinstance(event, events.Request):
                        rule = self.pending.pop(event.target.lstrip("/"), None)
                        if rule is None:
                            await send(events.RejectConnection(status_code=404))
                            return
                        config = rule["ws_config"]
                        await send(
                            events.AcceptConnection(
                                extra_headers=[
                                    (b"X-Mock-Rule", rule["id"].encode("ascii")),
                                ]
                            )
                        )
                        accepted = True
                        if config.get("protocol", "raw") == "socketio":
                            await send(
                                events.TextMessage(
                                    data="0"
                                    + json.dumps(
                                        {
                                            "sid": sid,
                                            "upgrades": [],
                                            "pingInterval": config.get(
                                                "ping_interval_ms", 25000
                                            ),
                                            "pingTimeout": config.get(
                                                "ping_timeout_ms", 20000
                                            ),
                                            "maxPayload": 1_048_576,
                                        }
                                    )
                                )
                            )
                            workers.append(asyncio.create_task(run_worker(heartbeat())))
                        else:
                            connected.set()
                        workers.append(asyncio.create_task(run_worker(pushes())))
                    elif isinstance(event, events.Ping):
                        await send(event.response())
                    elif isinstance(event, events.CloseConnection):
                        await send(event.response())
                        return
                    elif isinstance(event, events.TextMessage):
                        text += event.data
                        if len(text.encode("utf-8")) > 1_048_576:
                            await send(
                                events.CloseConnection(
                                    code=1009, reason="message too large"
                                )
                            )
                            return
                        if not event.message_finished:
                            continue
                        if config.get("protocol") == "socketio":
                            if text == "3":
                                pong.set()
                            elif text.startswith("40"):
                                suffix = text[2:]
                                namespace = (
                                    suffix.split(",", 1)[0]
                                    if suffix.startswith("/")
                                    else ""
                                )
                                prefix = f"40{namespace}," if namespace else "40"
                                await send(
                                    events.TextMessage(
                                        data=prefix + json.dumps({"sid": sid})
                                    )
                                )
                                connected.set()
                            elif text == "1":
                                await send(events.CloseConnection(code=1000))
                                return
                        text = ""
        except (
            ConnectionError,
            TimeoutError,
            wsproto.utilities.LocalProtocolError,
            wsproto.utilities.RemoteProtocolError,
        ):
            pass
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            finally:
                self.tasks.discard(task)


def socketio_error(url: str, config: dict) -> str | None:
    """Fail unsupported Engine.IO handshakes instead of faking a usable session.

    不支持的 Engine.IO 握手直接报错，避免伪造出实际无法通信的会话。
    """
    if config.get("protocol") == "socketio":
        query = parse_qs(urlsplit(url).query)
        if query.get("EIO") != ["4"] or query.get("transport") != ["websocket"]:
            return "Socket.IO mock 仅支持 EIO=4&transport=websocket 直连"
    return None
