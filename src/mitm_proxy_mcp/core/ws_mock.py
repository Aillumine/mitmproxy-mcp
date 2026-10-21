import math
import re
from typing import Any

MOCK_TYPES = ("http", "ws_message", "ws_server", "ws_fault")


def validate_ws_config(mock_type: str, config: Any) -> None:
    """Reject invalid actions and timers before a live proxy can consume them.

    在保存时拒绝非法动作与定时参数，避免运行中的代理读取到不可执行的规则。
    """
    if mock_type not in MOCK_TYPES:
        raise ValueError("mock_type 必须是 http / ws_message / ws_server / ws_fault")
    if not isinstance(config, dict):
        raise ValueError("ws_config 必须是对象")
    if mock_type == "http":
        return
    if mock_type == "ws_fault":
        validate_fault_config(config)
        return
    if mock_type == "ws_message":
        if config.get("direction", "server") not in ("client", "server", "both"):
            raise ValueError("direction 必须是 client / server / both")
        if config.get("action", "replace") not in ("replace", "drop", "reply"):
            raise ValueError("action 必须是 replace / drop / reply")
        if config.get("message_match_type", "contains") not in (
            "contains",
            "exact",
            "regex",
        ):
            raise ValueError("message_match_type 必须是 contains / exact / regex")
        pattern = config.get("message_pattern", "")
        if not isinstance(pattern, str):
            raise ValueError("message_pattern 必须是字符串")
        if config.get("message_match_type") == "regex":
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"消息正则无效: {exc}") from exc
    else:
        if config.get("protocol", "raw") not in ("raw", "socketio"):
            raise ValueError("protocol 必须是 raw / socketio")
        messages = config.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 1000:
            raise ValueError("messages 必须是数组，最多 1000 条")
        for item in messages:
            if not isinstance(item, dict) or not isinstance(item.get("body"), str):
                raise ValueError("推送必须包含字符串 body")
            _milliseconds(item.get("delay_ms", 0), "推送 delay_ms", minimum=0)
        for key, default in (("ping_interval_ms", 25000), ("ping_timeout_ms", 20000)):
            _milliseconds(config.get(key, default), key, minimum=1)


def _milliseconds(value: Any, name: str, *, minimum: int) -> None:
    if type(value) is not int or not minimum <= value <= 86_400_000:
        raise ValueError(f"{name} 必须是 {minimum} 到 86400000 的整数")


def validate_fault_config(config: dict) -> None:
    """Validate timed faults before creating connection-owned scheduling tasks.

    在创建连接所属的定时任务前校验故障参数，拒绝非法关闭码与无法执行的触发条件。
    """
    if config.get("direction", "server") not in ("client", "server", "both"):
        raise ValueError("direction 必须是 client / server / both")
    if config.get("action") not in ("delay", "pause", "close", "abort"):
        raise ValueError("故障 action 必须是 delay / pause / close / abort")
    if config.get("trigger", "messages") not in ("time", "messages"):
        raise ValueError("trigger 必须是 time / messages")
    _milliseconds(config.get("after_ms", 0), "after_ms", minimum=0)
    count = config.get("after_messages", 0)
    if type(count) is not int or not 0 <= count <= 1_000_000:
        raise ValueError("after_messages 必须是 0 到 1000000 的整数")
    if config.get("action") == "pause":
        _milliseconds(config.get("duration_ms"), "duration_ms", minimum=1)
    _milliseconds(config.get("jitter_ms", 0), "jitter_ms", minimum=0)
    probability = config.get("probability", 1)
    if (
        type(probability) not in (int, float)
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
    ):
        raise ValueError("probability 必须是 0 到 1 的数字")
    if type(config.get("preserve_heartbeat", True)) is not bool:
        raise ValueError("preserve_heartbeat 必须是布尔值")
    if "seed" in config and (
        type(config["seed"]) is not int or not 0 <= config["seed"] <= 4_294_967_295
    ):
        raise ValueError("seed 必须是 0 到 4294967295 的整数")
    code = config.get("close_code", 1000)
    if type(code) is not int or not (
        code in (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014)
        or 3000 <= code <= 4999
    ):
        raise ValueError("close_code 必须是可发送的 WebSocket 关闭码")
    reason = config.get("close_reason", "mock fault")
    if not isinstance(reason, str) or len(reason.encode("utf-8")) > 123:
        raise ValueError("close_reason 必须是 UTF-8 编码不超过 123 字节的字符串")


def validate_mock_values(
    mock_type: str, config: Any, match_type: str, url_pattern: str, delay_ms: int
) -> None:
    validate_ws_config(mock_type, config)
    _milliseconds(delay_ms, "delay_ms", minimum=0)
    if match_type not in ("contains", "exact", "regex"):
        raise ValueError("match_type 必须是 contains / exact / regex")
    if match_type == "regex":
        try:
            re.compile(url_pattern)
        except re.error as exc:
            raise ValueError(f"URL 正则无效: {exc}") from exc
