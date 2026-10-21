import re
from typing import Any

MOCK_TYPES = ("http", "ws_message", "ws_server")


def validate_ws_config(mock_type: str, config: Any) -> None:
    """Reject invalid actions and timers before a live proxy can consume them.

    在保存时拒绝非法动作与定时参数，避免运行中的代理读取到不可执行的规则。
    """
    if mock_type not in MOCK_TYPES:
        raise ValueError("mock_type 必须是 http / ws_message / ws_server")
    if not isinstance(config, dict):
        raise ValueError("ws_config 必须是对象")
    if mock_type == "http":
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
