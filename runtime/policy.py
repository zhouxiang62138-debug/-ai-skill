"""由配置驱动的 Runtime 提交权限校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]


def load_field_ownership() -> dict[str, frozenset[str]]:
    """读取唯一的角色字段归属配置，配置损坏即阻止启动。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
    )
    raw = document.get("field_ownership")
    if not isinstance(raw, dict):
        raise RuntimeValidationError("ROLE_POLICY_INVALID")
    result: dict[str, frozenset[str]] = {}
    for actor, fields in raw.items():
        if not isinstance(actor, str) or not isinstance(fields, list) or not all(
            isinstance(item, str) for item in fields
        ):
            raise RuntimeValidationError("ROLE_POLICY_INVALID")
        result[actor] = frozenset(fields)
    return result


def changed_top_level_fields(before: dict[str, Any], after: dict[str, Any]) -> frozenset[str]:
    """计算完整候选状态的显式顶层 diff。"""

    return frozenset(
        field
        for field in set(before) | set(after)
        if before.get(field) != after.get(field)
    )


def assert_field_ownership(
    actor: str, before: dict[str, Any], after: dict[str, Any]
) -> None:
    """拒绝角色越权、隐藏字段变更以及 Runtime 业务字段变更。"""

    allowed = load_field_ownership().get(actor)
    if allowed is None:
        raise RuntimeValidationError("ROLE_POLICY_UNKNOWN_ACTOR")
    changed = changed_top_level_fields(before, after)
    forbidden = changed - allowed
    if forbidden:
        raise RuntimeValidationError(
            "ROLE_FIELD_OWNERSHIP_VIOLATION:" + ",".join(sorted(forbidden))
        )
