"""根据业务状态确定性选择现有角色、Module 或等待。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import RuntimeValidationError


WAIT_STATUSES = {
    "WAITING_FOR_REQUIREMENTS",
    "WAITING_FOR_DESIGN_REVIEW",
    "WAITING_FOR_PRODUCT_REVIEW",
    "WAITING_FOR_PLAN_REVIEW",
    "WAITING_FOR_CHANGE_APPROVAL",
    "WAITING_FOR_USER",
    "BLOCKED",
    "ACCEPTED",
    "ARCHIVED",
}
ROLE_BY_STATUS = {
    "PLANNING": "planner",
    "DESIGN_EXPLORATION": "planner",
    "PLANNING_REVISION": "planner",
    "APPROVED_FOR_IMPLEMENTATION": "generator",
    "IMPLEMENTING": "generator",
    "EVALUATING": "evaluator",
    "CHANGE_REQUESTED": "planner",
    "RELEASE_READY": "evaluator",
}


@dataclass(frozen=True)
class Selection:
    kind: str
    target: str | None
    reason: str


def select_role(state: dict[str, Any]) -> Selection:
    """只根据受验证字段选择目标，不进行产品或验收决策。"""

    status = state.get("status")
    module = state.get("active_module")
    next_role = state.get("next_role")
    if status in WAIT_STATUSES:
        return Selection("WAIT", None, f"status:{status}")
    if module == "first_ask_intake":
        if next_role is not None:
            raise RuntimeValidationError("First-Ask Module 不得同时声明 next_role")
        return Selection("MODULE", "first_ask_intake", "active_module")
    expected = ROLE_BY_STATUS.get(str(status))
    if expected is None:
        raise RuntimeValidationError(f"状态没有确定性 Runtime 路由：{status}")
    if next_role != expected:
        raise RuntimeValidationError(
            f"状态 {status} 要求 next_role={expected}，实际为 {next_role}"
        )
    return Selection("ROLE", expected, f"status:{status}")
