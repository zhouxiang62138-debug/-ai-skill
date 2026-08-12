"""需求发现一轮的编排器；不负责 Runtime 状态提交。"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping

from .core import (
    analyze_gaps,
    analyze_initial_intent,
    build_coverage_map,
    evaluate_research_necessity,
    evaluate_sufficiency,
    prioritize_questions,
)


_UNDECIDED = ("不确定", "不知道", "还没决定", "尚未决定", "没想好", "未确定")

_DIMENSION_TO_FIELD = {
    "product_intent": "primary_goal",
    "target_users": "target_users",
    "primary_goal": "primary_goal",
    "primary_use_cases": "use_cases",
    "core_workflows": "use_cases",
    "platform": "platform",
    "required_features": "required_features",
    "optional_features": "optional_features",
    "data_model": "data_sources",
    "technical_constraints": "technical_constraints",
    "design_preferences": "design_preferences",
}


def empty_requirements_snapshot(project_id: str, *, version: int, request_ref: str, user_text: str, created_at: str) -> dict[str, Any]:
    """创建只包含用户事实容器的初始快照；候选分析另存为 Intent。"""

    def field(key: str, value: Any = None, *, status: str = "unanswered", decision_impact: str = "high", safe: bool = False) -> dict[str, Any]:
        return {
            "question_key": key,
            "value": value,
            "status": status,
            "source_refs": [request_ref] if status != "unanswered" else [],
            "assumption_reason": None,
            "decision_impact": decision_impact,
            "architecture_impact": decision_impact,
            "workflow_impact": decision_impact,
            "safe_assumption_available": safe,
            "research_refs": [],
            "last_question_key": None,
        }

    # 用户说出的目标本身是事实；领域实体和工作流候选仍留在 Intent 层。
    goal_status = "answered" if user_text.strip() else "unanswered"
    snapshot = {
        "schema_version": 2,
        "requirement_version": version,
        "project_id": project_id,
        "created_at": created_at,
        "supersedes": None,
        "source_interviews": [],
        "original_request": {"ref": request_ref, "text": user_text},
        "references": [],
        "discovery": {
            "schema_version": 1,
            "status": "intent_analyzed",
            "intent_analysis_ref": None,
            "research_requirement_ref": None,
            "research_status": "not_started",
            "active_research_round": None,
            "research_refs": [],
            "coverage_map_ref": None,
            "gap_analysis_ref": None,
            "question_set_ref": None,
            "sufficiency_evaluation_ref": None,
            "opportunity_map_ref": None,
        },
        "target_users": field("target_users.primary"),
        "primary_goal": field("primary_goal.main", user_text if goal_status == "answered" else None, status=goal_status),
        "use_cases": field("use_cases.core", [user_text] if goal_status == "answered" else [], status=goal_status),
        "platform": field("platform.primary", [], decision_impact="critical", safe=False),
        "required_features": field("required_features.must_have", [], decision_impact="critical", safe=False),
        "optional_features": field("optional_features.nice_to_have", [], decision_impact="low", safe=True),
        "data_sources": field("data_sources.primary", [], decision_impact="critical", safe=False),
        "technical_constraints": field("technical_constraints.hard_limits", [], decision_impact="critical", safe=True),
        "design_preferences": field("design_preferences.explicit", None, decision_impact="low", safe=True) | {"routing": None, "specification_completeness": "none"},
        "undecided_items": [],
        "assumptions": [],
        "conflicts": [],
        "open_questions": [],
        "completion_assessment": {"critical_fields_ready": False, "blocking_fields": [], "visual_undecided_routed": False, "notes": None},
        "discovery_status": "intent_analyzed",
        "requirements_status": "interviewing",
    }
    return snapshot


def apply_question_answers(snapshot: Mapping[str, Any], question_set: Mapping[str, Any], user_text: str, *, interview_ref: str) -> dict[str, Any]:
    """将普通会话的答案按本轮问题顺序写入新快照。

    宿主如果能提供结构化回答，可在外层先转换为同样的 question_key 映射；这里的
    顺序解析只是无扩展输入适配器时的保守后备，不会修改历史快照。
    """

    updated = copy.deepcopy(dict(snapshot))
    questions = [item for item in question_set.get("questions", []) if isinstance(item, Mapping)]
    lines = [line.strip() for line in re.split(r"\r?\n", user_text) if line.strip()]
    if not lines:
        lines = [user_text.strip()]
    if len(questions) == 1:
        answers = [user_text.strip()]
    else:
        answers = []
        for line in lines:
            cleaned = re.sub(r"^(?:\d+[.、)]|[-*])\s*", "", line).strip()
            if cleaned:
                answers.append(cleaned)
        answers.extend([""] * (len(questions) - len(answers)))
    for question, answer in zip(questions, answers):
        dimension_key = str(question.get("field_key") or "")
        key = _DIMENSION_TO_FIELD.get(dimension_key, dimension_key)
        if not key or key not in updated or not isinstance(updated[key], Mapping):
            continue
        value = answer
        if key in {"use_cases", "required_features", "optional_features", "platform", "data_sources", "technical_constraints"}:
            value = [part.strip() for part in re.split(r"[,，、;；]", answer) if part.strip()]
        status = "undecided" if any(marker in answer for marker in _UNDECIDED) else "answered"
        field = dict(updated[key])
        field.update({"value": value, "status": status, "source_refs": list(field.get("source_refs") or []) + [f"{interview_ref}#answer-{key}"], "last_question_key": question.get("question_key")})
        if status == "undecided":
            updated.setdefault("undecided_items", []).append({"field": key, "routing": "design_exploration" if key == "design_preferences" else "later_product_discovery", "source_ref": interview_ref})
        updated[key] = field
    updated.setdefault("source_interviews", []).append(interview_ref)
    updated["supersedes"] = snapshot.get("active_ref") or snapshot.get("_self_ref")
    return updated


def run_discovery_cycle(
    snapshot: Mapping[str, Any],
    *,
    project_id: str,
    requirements_ref: str,
    request_text: str,
    round_number: int,
    history_question_keys: Iterable[str] = (),
    research_status: str = "not_required",
    intent: Mapping[str, Any] | None = None,
    research_requirement: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """完整执行一轮：Intent → Gate → Coverage → Gap → Question → Sufficiency。"""

    current_intent = dict(intent or analyze_initial_intent(request_text, project_id=project_id, source_request_ref=str(snapshot.get("original_request", {}).get("ref") or requirements_ref), created_at=created_at))
    gate = dict(research_requirement or evaluate_research_necessity(current_intent, user_text=request_text, created_at=created_at))
    coverage = build_coverage_map(snapshot, project_id=project_id, requirements_ref=requirements_ref, intent=current_intent, research_refs=snapshot.get("discovery", {}).get("research_refs", []), created_at=created_at)
    gaps = analyze_gaps(coverage, project_id=project_id, created_at=created_at)
    question_set = prioritize_questions(coverage, gaps, project_id=project_id, round_number=round_number, history_question_keys=history_question_keys, created_at=created_at)
    sufficiency = evaluate_sufficiency(coverage, project_id=project_id, snapshot_material=snapshot, research_decision=str(gate.get("decision")), research_status=research_status, risk_level=str(current_intent.get("risk_level") or "unknown"), created_at=created_at)
    return {"intent": current_intent, "research_requirement": gate, "coverage": coverage, "gaps": gaps, "question_set": question_set, "sufficiency": sufficiency}
