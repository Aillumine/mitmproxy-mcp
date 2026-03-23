"""
Mock 工具

提供 mock 规则的增删改查功能。
通过 MCP 工具调用管理 mock 规则。
"""

import json
import time
import uuid
from typing import Any

from ..core.mock_store import MockStore, MockRule


def _get_mock_store() -> MockStore:
    return MockStore()


def mock_add(
    name: str,
    url_pattern: str,
    response_body: str,
    method: str = "",
    match_type: str = "contains",
    status_code: int = 200,
    response_headers: dict[str, str] | None = None,
    delay_ms: int = 0,
    enabled: bool = True,
) -> dict[str, Any]:
    """
    添加 mock 规则

    Args:
        name: 规则名称（便于识别）
        url_pattern: URL 匹配模式
        response_body: mock 响应体（JSON 字符串）
        method: HTTP 方法过滤，空字符串表示匹配所有
        match_type: 匹配方式 contains|exact|regex
        status_code: 响应状态码
        response_headers: 响应头
        delay_ms: 模拟延迟（毫秒）
        enabled: 是否启用

    Returns:
        包含新规则信息的字典
    """
    if match_type not in ("contains", "exact", "regex"):
        return {
            "success": False,
            "message": f"无效的 match_type: {match_type}，支持 contains/exact/regex",
        }

    store = _get_mock_store()

    rule = MockRule(
        id=f"mock-{uuid.uuid4().hex[:8]}",
        name=name,
        url_pattern=url_pattern,
        method=method.upper() if method else "",
        match_type=match_type,
        status_code=status_code,
        response_headers=response_headers or {"content-type": "application/json"},
        response_body=response_body,
        delay_ms=delay_ms,
        enabled=enabled,
    )

    store.add(rule)

    return {
        "success": True,
        "message": f"Mock 规则已添加: {name}",
        "rule": rule.to_dict(),
    }


def mock_list(enabled_only: bool = False) -> dict[str, Any]:
    """
    列出所有 mock 规则

    Args:
        enabled_only: 是否只列出启用的规则

    Returns:
        包含规则列表的字典
    """
    store = _get_mock_store()
    rules = store.list_all(enabled_only=enabled_only)

    return {
        "success": True,
        "rules": [r.to_dict() for r in rules],
        "total": len(rules),
        "enabled_count": sum(1 for r in rules if r.enabled),
    }


def mock_update(
    rule_id: str,
    name: str | None = None,
    url_pattern: str | None = None,
    response_body: str | None = None,
    method: str | None = None,
    match_type: str | None = None,
    status_code: int | None = None,
    response_headers: dict[str, str] | None = None,
    delay_ms: int | None = None,
) -> dict[str, Any]:
    """
    更新 mock 规则

    Args:
        rule_id: 规则 ID
        其他参数: 需要更新的字段，None 表示不更新

    Returns:
        更新结果
    """
    store = _get_mock_store()

    kwargs: dict[str, Any] = {}
    if name is not None:
        kwargs["name"] = name
    if url_pattern is not None:
        kwargs["url_pattern"] = url_pattern
    if response_body is not None:
        kwargs["response_body"] = response_body
    if method is not None:
        kwargs["method"] = method.upper() if method else ""
    if match_type is not None:
        if match_type not in ("contains", "exact", "regex"):
            return {
                "success": False,
                "message": f"无效的 match_type: {match_type}，支持 contains/exact/regex",
            }
        kwargs["match_type"] = match_type
    if status_code is not None:
        kwargs["status_code"] = status_code
    if response_headers is not None:
        kwargs["response_headers"] = response_headers
    if delay_ms is not None:
        kwargs["delay_ms"] = delay_ms

    if not kwargs:
        return {
            "success": False,
            "message": "没有提供需要更新的字段",
        }

    success = store.update(rule_id, **kwargs)

    if not success:
        return {
            "success": False,
            "message": f"规则不存在: {rule_id}",
        }

    rule = store.get_by_id(rule_id)
    return {
        "success": True,
        "message": f"Mock 规则已更新: {rule_id}",
        "rule": rule.to_dict() if rule else None,
    }


def mock_delete(rule_id: str) -> dict[str, Any]:
    """
    删除 mock 规则

    Args:
        rule_id: 规则 ID

    Returns:
        删除结果
    """
    store = _get_mock_store()
    success = store.delete(rule_id)

    if not success:
        return {
            "success": False,
            "message": f"规则不存在: {rule_id}",
        }

    return {
        "success": True,
        "message": f"Mock 规则已删除: {rule_id}",
    }


def mock_toggle(rule_id: str, enabled: bool | None = None) -> dict[str, Any]:
    """
    启用/禁用 mock 规则

    Args:
        rule_id: 规则 ID
        enabled: 是否启用，None 则切换当前状态

    Returns:
        操作结果
    """
    store = _get_mock_store()
    rule = store.get_by_id(rule_id)

    if not rule:
        return {
            "success": False,
            "message": f"规则不存在: {rule_id}",
        }

    new_state = enabled if enabled is not None else (not rule.enabled)
    store.update(rule_id, enabled=new_state)

    return {
        "success": True,
        "message": f"Mock 规则已{'启用' if new_state else '禁用'}: {rule.name}",
        "rule_id": rule_id,
        "enabled": new_state,
    }


def mock_clear() -> dict[str, Any]:
    """
    清空所有 mock 规则

    Returns:
        清空结果
    """
    store = _get_mock_store()
    count = store.clear()

    return {
        "success": True,
        "message": f"已清空 {count} 条 mock 规则",
        "cleared_count": count,
    }


def mock_export() -> dict[str, Any]:
    """
    导出所有 mock 规则为 JSON（包含完整 response_body）

    Returns:
        包含全量规则的字典，可直接传给 mock_import
    """
    store = _get_mock_store()
    rules = store.list_all()

    exported = []
    for r in rules:
        exported.append({
            "name": r.name,
            "url_pattern": r.url_pattern,
            "method": r.method,
            "match_type": r.match_type,
            "status_code": r.status_code,
            "response_headers": r.response_headers,
            "response_body": r.response_body,
            "delay_ms": r.delay_ms,
            "enabled": r.enabled,
        })

    return {
        "success": True,
        "rules": exported,
        "total": len(exported),
    }


def mock_import(rules_json: str, merge: bool = False) -> dict[str, Any]:
    """
    从 JSON 导入 mock 规则

    Args:
        rules_json: JSON 字符串，格式为规则数组或 {"rules": [...]}
        merge: True 则追加到现有规则，False 则先清空再导入（默认）

    Returns:
        导入结果
    """
    try:
        data = json.loads(rules_json)
    except json.JSONDecodeError as e:
        return {"success": False, "message": f"JSON 解析失败: {e}"}

    if isinstance(data, list):
        rules_data = data
    elif isinstance(data, dict) and "rules" in data:
        rules_data = data["rules"]
    else:
        return {
            "success": False,
            "message": "无效的导入格式，需要规则数组或包含 rules 字段的对象",
        }

    store = _get_mock_store()

    if not merge:
        store.clear()

    imported = 0
    errors = []
    for item in rules_data:
        try:
            rule = MockRule(
                id=f"mock-{uuid.uuid4().hex[:8]}",
                name=item.get("name", "imported rule"),
                url_pattern=item.get("url_pattern", ""),
                method=(item.get("method", "") or "").upper().replace("*", ""),
                match_type=item.get("match_type", "contains"),
                status_code=item.get("status_code", 200),
                response_headers=item.get("response_headers", {"content-type": "application/json"}),
                response_body=item.get("response_body", ""),
                delay_ms=item.get("delay_ms", 0),
                enabled=item.get("enabled", True),
            )
            store.add(rule)
            imported += 1
        except Exception as e:
            errors.append(str(e))

    return {
        "success": True,
        "message": f"导入完成：{imported} 条规则" + (f"，{len(errors)} 条失败" if errors else ""),
        "imported": imported,
        "errors": errors,
    }
