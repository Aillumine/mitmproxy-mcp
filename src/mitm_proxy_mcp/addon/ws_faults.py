import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import wsproto.events
from mitmproxy import ctx
from mitmproxy.proxy import commands
from mitmproxy.proxy.layer import Layer
from mitmproxy.proxy.layers.websocket import (
    WebsocketEndHook,
    WebsocketLayer,
    WebsocketMessageHook,
    WebSocketMessageInjected,
)
from mitmproxy.websocket import WebSocketMessage
from wsproto import ConnectionState

from mitm_proxy_mcp.core.throttle import is_keepalive_frame


class FaultEvent(WebSocketMessageInjected):
    rule_ids: tuple[str, ...] = ()
    close_code: int | None = None
    close_reason: str = ""
    on_forwarded = None


class FaultTransport:
    """Route faults through the live protocol stack, including TLS encryption.

    故障事件仍经过当前协议栈，确保 WSS 关闭帧正确加密，且不污染其他连接。
    """

    def __init__(self, flow):
        self.flow = flow
        self.proxy = ctx.master.addons.get("proxyserver")
        self.handler = self.proxy.connections[flow.client_conn.id]
        pending = [self.handler.layer]
        seen = set()
        while pending:
            layer = pending.pop()
            if id(layer) in seen:
                continue
            seen.add(id(layer))
            if isinstance(layer, WebsocketLayer) and layer.flow is flow:
                self.bind(layer)
                return
            pending.extend(layer.context.layers)
            for name in ("child_layer", "layer"):
                child = getattr(layer, name, None)
                if isinstance(child, Layer):
                    pending.append(child)
            for name in ("streams", "connections"):
                children = getattr(layer, name, {})
                if isinstance(children, dict):
                    pending.extend(c for c in children.values() if isinstance(c, Layer))
        raise RuntimeError("无法定位当前 WebSocket 协议层，不能执行故障规则")

    def bind(self, layer):
        original = layer.relay_messages

        def relay(event):
            # The public injector only supports data frames. A connection-local
            # adapter adds Close and replay tags without patching global classes.
            #
            # 公共注入接口只支持数据帧；仅为当前连接适配关闭帧和重放标记，
            # 不修改 mitmproxy 的全局类，避免干扰其他连接。
            if isinstance(event, FaultEvent) and event.close_code is not None:
                close = wsproto.events.CloseConnection(
                    code=event.close_code, reason=event.close_reason
                )
                for peer in (layer.client_ws, layer.server_ws):
                    if peer.state in (
                        ConnectionState.OPEN,
                        ConnectionState.REMOTE_CLOSING,
                    ):
                        yield peer.send2(close)
                    yield commands.CloseConnection(peer.conn)
                layer.flow.websocket.close_code = event.close_code
                layer.flow.websocket.close_reason = event.close_reason
                layer.flow.websocket.closed_by_client = False
                layer.flow.websocket.timestamp_end = time.time()
                yield WebsocketEndHook(layer.flow)
                layer.flow.live = False
                layer._handle_event = layer.done
                return
            messages = []
            for command in original(event):
                if isinstance(command, WebsocketMessageHook):
                    message = command.flow.websocket.messages[-1]
                    messages.append(message)
                    if isinstance(event, FaultEvent):
                        message.fault_rule_ids = event.rule_ids
                        message.fault_on_forwarded = event.on_forwarded
                yield command
            # A counted close must follow actual SendData, including delayed
            # replays; starting its timer in the hook could discard the Nth frame.
            #
            # 按条数关闭必须发生在真正发出消息之后，包括延迟重放；
            # 若在钩子中启动关闭任务，第 N 条可能尚未发送便被丢弃。
            for message in messages:
                callback = getattr(message, "fault_on_forwarded", None)
                if callback is not None:
                    del message.fault_on_forwarded
                    if not message.dropped:
                        callback()

        layer.relay_messages = relay

    def replay(self, message, rule_ids, on_forwarded=None):
        if self.flow.client_conn.id not in self.proxy.connections:
            return
        event = FaultEvent(self.flow, message)
        event.rule_ids = tuple(rule_ids)
        event.on_forwarded = on_forwarded
        self.proxy.inject_event(event)

    def terminate(self, config):
        if self.flow.client_conn.id not in self.proxy.connections:
            return
        if config["action"] == "close":
            event = FaultEvent(self.flow, WebSocketMessage(1, False, b""))
            event.close_code = config.get("close_code", 1000)
            event.close_reason = config.get("close_reason", "mock fault")
            self.proxy.inject_event(event)
        else:
            # Abort the transports before EOF can be translated into a WS Close.
            # This models abrupt loss, without promising a particular TCP flag.
            #
            # 在 EOF 被协议层转换成关闭帧前直接中断传输，模拟突然断连，
            # 但不承诺底层一定发送某种 TCP 标志位。
            for connection in (self.flow.client_conn, self.flow.server_conn):
                io = self.handler.transports.get(connection)
                if io is not None and io.writer is not None:
                    transport = getattr(io.writer, "transport", None)
                    if transport is not None:
                        transport.abort()
                    else:
                        io.writer.close()


@dataclass
class Policy:
    rule: dict
    rng: random.Random
    count: int = 0
    active: bool = False
    fired: bool = False
    selected: bool = True

    @property
    def config(self):
        return self.rule["ws_config"]


class FaultSession:
    """Schedule per-direction FIFO delivery without blocking WS heartbeats.

    按方向排队恢复原消息，避免阻塞消息钩子导致心跳无法通过，也避免消息乱序。
    """

    def __init__(self, flow, rules, transport, hit):
        self.flow = flow
        self.transport = transport
        self.hit = hit
        self.policies = []
        self.jobs = set()
        self.workers = {}
        self.queues = {True: deque(), False: deque()}
        self.wake = {True: asyncio.Event(), False: asyncio.Event()}
        self.pauses = {True: [], False: []}
        self.queued_bytes = 0
        self.closed = False
        self.terminating = False
        query = parse_qs(urlsplit(flow.request.pretty_url).query)
        self.socketio = query.get("EIO") in (["3"], ["4"])
        # One-shot faults draw once per connection; delays draw for each message.
        # Keeping an independent RNG makes a fixed seed repeat across reconnects.
        #
        # 一次性故障每条连接抽一次概率，延迟则按消息抽取；各规则独立的随机源
        # 让固定种子在重连后仍可重复同一序列。
        for rule in rules:
            config = rule["ws_config"]
            policy = Policy(rule, random.Random(config.get("seed")))
            if config["action"] != "delay":
                policy.selected = policy.rng.random() < config.get("probability", 1)
            self.policies.append(policy)
            if config.get("trigger", "messages") == "time":
                self.spawn(self.activate_later(policy))
            elif config["action"] in ("close", "abort") and not config.get(
                "after_messages", 0
            ):
                self.terminating = self.terminating or policy.selected
                self.spawn(self.terminate_later(policy))

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    async def activate_later(self, policy):
        await asyncio.sleep(policy.config.get("after_ms", 0) / 1000)
        if self.closed or not policy.selected:
            return
        policy.active = True
        if policy.config["action"] == "pause":
            self.pause(policy)
        elif policy.config["action"] in ("close", "abort"):
            self.terminate(policy)

    def pause(self, policy):
        policy.fired = True
        until = time.monotonic() + policy.config["duration_ms"] / 1000
        for direction in (True, False):
            if policy.config.get("direction", "server") in (
                "both",
                "client" if direction else "server",
            ):
                self.pauses[direction].append((until, policy))
                self.wake[direction].set()
        self.hit(policy.rule["id"])

    async def terminate_later(self, policy):
        # Let the current hook complete before closing, so the Nth message and
        # the upgrade response reach the peer before their scheduled disconnect.
        #
        # 先让当前钩子完成，确保第 N 条消息和升级响应先发出，再执行计划中的断连。
        await asyncio.sleep(0)
        if not self.closed and policy.selected:
            self.terminate(policy)

    def terminate(self, policy):
        self.hit(policy.rule["id"])
        self.stop()
        self.transport.terminate(policy.config)

    def close_after_forward(self, policies):
        for policy in policies:
            if not self.closed:
                self.terminate(policy)

    def apply(self, message):
        if self.closed:
            message.drop()
            return True
        if hasattr(message, "fault_rule_ids") or message.dropped:
            return False
        now = time.monotonic()
        release_at = now
        rule_ids = set()
        from_client = message.from_client
        heartbeat = self.socketio and is_keepalive_frame(message.content)
        if self.terminating:
            if heartbeat and all(
                p.config.get("preserve_heartbeat", True) for p in self.policies
            ):
                return False
            message.drop()
            return True
        close_pending = []
        applicable = False
        for policy in self.policies:
            config = policy.config
            if config.get("direction", "server") not in (
                "both",
                "client" if from_client else "server",
            ) or (heartbeat and config.get("preserve_heartbeat", True)):
                continue
            applicable = True
            policy.count += 1
            if config.get("trigger", "messages") == "messages":
                if config["action"] in ("close", "abort"):
                    if not policy.fired and policy.count >= config.get(
                        "after_messages", 0
                    ):
                        policy.fired = True
                        self.terminating = self.terminating or policy.selected
                        if policy.selected:
                            close_pending.append(policy)
                    continue
                policy.active = policy.count > config.get("after_messages", 0)
            if not policy.active or not policy.selected:
                continue
            if config["action"] == "pause" and not policy.fired:
                self.pause(policy)
            elif config["action"] == "delay":
                if policy.rng.random() < config.get("probability", 1):
                    jitter = config.get("jitter_ms", 0)
                    delay = max(
                        0,
                        policy.rule.get("delay_ms", 0)
                        + policy.rng.uniform(-jitter, jitter),
                    )
                    release_at = max(release_at, now + delay / 1000)
                    rule_ids.add(policy.rule["id"])
                    self.hit(policy.rule["id"])
        if not applicable:
            return False
        active_pauses = [
            (until, policy)
            for until, policy in self.pauses[from_client]
            if until > now
            and not (heartbeat and policy.config.get("preserve_heartbeat", True))
        ]
        pause_until = max((until for until, _ in active_pauses), default=now)
        rule_ids.update(policy.rule["id"] for _, policy in active_pauses)
        queue = self.queues[from_client]
        # Exempt heartbeats may pass a business backlog; ordinary data must keep
        # its order even when the next frame has no matching delay policy.
        #
        # 豁免的心跳可以越过业务队列；普通消息即使未命中延迟也不能越过积压的消息。
        if release_at <= now and pause_until <= now and not queue:
            if close_pending:
                message.fault_on_forwarded = lambda: self.close_after_forward(
                    close_pending
                )
            return False
        message.drop()
        if (
            self.queued_bytes + len(message.content) > 8 * 1024 * 1024
            or sum(len(q) for q in self.queues.values()) >= 1000
        ):
            # Bound retained messages so a long pause cannot exhaust proxy memory.
            #
            # 限制暂存消息总量，避免长时间暂停耗尽代理内存。
            self.stop()
            self.transport.terminate(
                {
                    "action": "close",
                    "close_code": 1009,
                    "close_reason": "mock queue limit",
                }
            )
            return True
        copy = WebSocketMessage(message.type, from_client, message.content)
        self.queued_bytes += len(copy.content)
        queue.append((copy, release_at, rule_ids, heartbeat, close_pending))
        if from_client not in self.workers:
            self.workers[from_client] = self.spawn(self.drain(from_client))
        self.wake[from_client].set()
        return True

    async def drain(self, direction):
        try:
            queue = self.queues[direction]
            while queue and not self.closed:
                message, release_at, rule_ids, heartbeat, close_pending = queue[0]
                pause_until = max(
                    (
                        until
                        for until, policy in self.pauses[direction]
                        if not (
                            heartbeat and policy.config.get("preserve_heartbeat", True)
                        )
                    ),
                    default=0,
                )
                delay = max(release_at, pause_until) - time.monotonic()
                if delay > 0:
                    self.wake[direction].clear()
                    try:
                        await asyncio.wait_for(self.wake[direction].wait(), delay)
                    except TimeoutError:
                        pass
                    continue
                queue.popleft()
                self.queued_bytes -= len(message.content)
                if close_pending:
                    self.transport.replay(
                        message,
                        rule_ids,
                        lambda policies=close_pending: self.close_after_forward(
                            policies
                        ),
                    )
                else:
                    self.transport.replay(message, rule_ids)
        finally:
            self.workers.pop(direction, None)

    def stop(self):
        self.closed = True
        current = asyncio.current_task()
        for job in list(self.jobs):
            if job is not current:
                job.cancel()
        for queue in self.queues.values():
            queue.clear()
        self.queued_bytes = 0
