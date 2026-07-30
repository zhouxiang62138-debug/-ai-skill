"""F9.3 增量复验、回归、趋势、提前升级与最大迭代治理。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_protocol import ProjectStateError, issue_id, validate_relative_path
from project_state import serialize_project_state


PROGRESS_STATUSES = ("IMPROVING", "STABLE", "REGRESSING", "STALLED", "UNKNOWN")
DEFAULT_ESCALATION_RULES = {
    "repeated_same_issue": 2,
    "repeated_failed_fix_claim": 2,
    "consecutive_regression_rounds": 2,
    "routing_disagreement_rounds": 2,
    "no_progress_rounds": 2,
}
MAXIMUM_AUTOMATIC_ITERATIONS = 5


def _open_issues(package: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in package.get("issues", [])
        if isinstance(item, dict) and item.get("status") in {"OPEN", "REOPENED"}
    ]


def issue_signature(issue: dict[str, Any]) -> str:
    """同一根因签名；禁止通过轻微改名逃避历史 ID。"""
    root_cause = issue.get("root_cause_key")
    if not isinstance(root_cause, str) or not root_cause.strip():
        root_cause = "|".join(
            str(issue.get(field) or "").strip().lower()
            for field in (
                "category",
                "requirement_id",
                "acceptance_criterion_id",
                "expected_result",
                "actual_result",
            )
        )
    return "|".join(
        (
            str(issue.get("requirement_id") or "NA"),
            str(issue.get("acceptance_criterion_id") or "NA"),
            root_cause.strip().lower(),
        )
    )


def assign_stable_issue_identity(
    candidate: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    evaluation_id: str,
    sequence: int,
) -> dict[str, Any]:
    """复用历史根因 ID；新根因才分配当前 Evaluation 的新 ID。"""
    current = deepcopy(candidate)
    signature = issue_signature(current)
    matches = [
        item
        for item in history
        if isinstance(item, dict) and item.get("signature") == signature
    ]
    if matches:
        latest = matches[-1]
        current["issue_id"] = latest["issue_id"]
        current["first_seen_evaluation"] = latest["first_seen_evaluation"]
        current["status"] = (
            "REOPENED" if latest.get("status") == "RESOLVED" else "OPEN"
        )
    else:
        current["issue_id"] = issue_id(evaluation_id, sequence)
        current["first_seen_evaluation"] = evaluation_id
        current.setdefault("status", "OPEN")
    current["root_cause_key"] = signature
    return current


def select_incremental_rechecks(
    previous_package: dict[str, Any],
    generator_response: dict[str, Any],
    changed_files: list[str],
    acceptance_file_map: dict[str, list[str]],
) -> dict[str, list[str]]:
    """选择上轮开放/复发/声称修复项和受改动文件影响的验收标准。"""
    for path in changed_files:
        path_error = validate_relative_path(path)
        if path_error:
            raise ProjectStateError(f"changed_files 中的 {path!r} {path_error}")
    response_status = {
        item.get("issue_id"): item.get("status")
        for item in generator_response.get("issue_responses", [])
        if isinstance(item, dict)
    }
    selected_issues: list[str] = []
    for item in previous_package.get("issues", []):
        current_id = item.get("issue_id")
        if (
            item.get("status") in {"OPEN", "REOPENED"}
            or response_status.get(current_id)
            in {"FIXED", "PARTIALLY_FIXED", "CANNOT_REPRODUCE"}
        ):
            if current_id not in selected_issues:
                selected_issues.append(current_id)
    selected_criteria = sorted(
        criterion
        for criterion, paths in acceptance_file_map.items()
        if set(paths) & set(changed_files)
    )
    related_tests = sorted(
        {
            command
            for item in previous_package.get("issues", [])
            if item.get("issue_id") in selected_issues
            for command in item.get("related_test_ids", [])
        }
    )
    return {
        "issue_ids": selected_issues,
        "acceptance_criterion_ids": selected_criteria,
        "related_test_ids": related_tests,
    }


def build_verification_plan(
    targeted: dict[str, list[str]],
    mandatory_regression_suite: list[str],
) -> dict[str, Any]:
    if not isinstance(mandatory_regression_suite, list) or not all(
        isinstance(item, str) and item for item in mandatory_regression_suite
    ):
        raise ProjectStateError("mandatory_regression_suite 必须是非空字符串列表")
    return {
        "targeted_rechecks": deepcopy(targeted),
        "mandatory_regression_suite": list(dict.fromkeys(mandatory_regression_suite)),
        "mandatory_regression_may_be_skipped": False,
    }


def compute_iteration_metrics(
    previous_package: dict[str, Any] | None,
    current_package: dict[str, Any],
) -> dict[str, Any]:
    current_open = _open_issues(current_package)
    if previous_package is None:
        return {
            "previous_total_issues": 0,
            "previous_blockers": 0,
            "previous_critical": 0,
            "resolved_issues": 0,
            "reopened_issues": sum(
                item.get("status") == "REOPENED" for item in current_open
            ),
            "repeated_issues": 0,
            "new_issues": len(current_open),
            "new_regressions": sum(
                item.get("category") == "regression" for item in current_open
            ),
            "current_total_issues": len(current_open),
            "current_blockers": sum(
                item.get("severity") == "blocker" for item in current_open
            ),
            "current_critical": sum(
                item.get("severity") == "critical" for item in current_open
            ),
            "progress_status": "UNKNOWN",
        }
    previous_open = _open_issues(previous_package)
    previous_ids = {item["issue_id"] for item in previous_open}
    current_ids = {item["issue_id"] for item in current_open}
    resolved_ids = (previous_ids - current_ids) | {
        item.get("issue_id")
        for item in current_package.get("issues", [])
        if item.get("issue_id") in previous_ids and item.get("status") == "RESOLVED"
    }
    resolved = len(resolved_ids)
    repeated = len(previous_ids & current_ids)
    new = len(current_ids - previous_ids)
    previous_blockers = sum(
        item.get("severity") == "blocker" for item in previous_open
    )
    previous_critical = sum(
        item.get("severity") == "critical" for item in previous_open
    )
    current_blockers = sum(
        item.get("severity") == "blocker" for item in current_open
    )
    current_critical = sum(
        item.get("severity") == "critical" for item in current_open
    )
    regressions = sum(item.get("category") == "regression" for item in current_open)
    if (
        regressions > 0
        or current_blockers > previous_blockers
        or current_critical > previous_critical
        or (len(current_open) > len(previous_open) and new >= resolved)
    ):
        progress = "REGRESSING"
    elif (
        resolved > 0
        and (
            len(current_open) < len(previous_open)
            or current_blockers < previous_blockers
            or current_critical < previous_critical
        )
    ):
        progress = "IMPROVING"
    elif repeated > 0 and resolved == 0 and new == 0:
        progress = "STALLED"
    else:
        progress = "STABLE"
    return {
        "previous_total_issues": len(previous_open),
        "previous_blockers": previous_blockers,
        "previous_critical": previous_critical,
        "resolved_issues": resolved,
        "reopened_issues": sum(
            item.get("status") == "REOPENED" for item in current_open
        ),
        "repeated_issues": repeated,
        "new_issues": new,
        "new_regressions": regressions,
        "current_total_issues": len(current_open),
        "current_blockers": current_blockers,
        "current_critical": current_critical,
        "progress_status": progress,
    }


def update_repeat_history(
    prior_history: list[dict[str, Any]],
    current_package: dict[str, Any],
    *,
    stalled_threshold: int,
    failed_fix_claims: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    if stalled_threshold < 1:
        raise ProjectStateError("stalled_threshold 必须为正整数")
    by_id = {
        item["issue_id"]: deepcopy(item)
        for item in prior_history
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }
    evaluation_id = current_package["evaluation_id"]
    for issue in _open_issues(current_package):
        current_id = issue["issue_id"]
        record = by_id.get(
            current_id,
            {
                "issue_id": current_id,
                "signature": issue_signature(issue),
                "repeat_count": 0,
                "failed_fix_claim_count": 0,
                "first_seen_evaluation": issue.get(
                    "first_seen_evaluation", evaluation_id
                ),
            },
        )
        record["repeat_count"] += 1
        if (failed_fix_claims or {}).get(current_id):
            record["failed_fix_claim_count"] += 1
        record["last_seen_evaluation"] = evaluation_id
        record["status"] = issue.get("status")
        record["stalled_issue"] = record["repeat_count"] >= stalled_threshold
        by_id[current_id] = record
    for issue in current_package.get("issues", []):
        if issue.get("status") == "RESOLVED" and issue.get("issue_id") in by_id:
            by_id[issue["issue_id"]]["status"] = "RESOLVED"
            by_id[issue["issue_id"]]["last_seen_evaluation"] = evaluation_id
    return sorted(by_id.values(), key=lambda item: item["issue_id"])


def detect_regressions(
    previous_checks: list[dict[str, Any]],
    current_checks: list[dict[str, Any]],
    *,
    evaluation_id: str,
    starting_sequence: int,
    changed_files: list[str],
) -> list[dict[str, Any]]:
    previous = {
        (
            item.get("requirement_id"),
            item.get("acceptance_criterion_id"),
        ): item
        for item in previous_checks
        if item.get("result") == "PASS"
    }
    regressions: list[dict[str, Any]] = []
    sequence = starting_sequence
    for current in current_checks:
        key = (
            current.get("requirement_id"),
            current.get("acceptance_criterion_id"),
        )
        if key not in previous or current.get("result") != "FAIL":
            continue
        old = previous[key]
        regressions.append(
            {
                "issue_id": issue_id(evaluation_id, sequence),
                "category": "regression",
                "severity": "critical",
                "title": f"已通过验收项发生回归：{key[1]}",
                "requirement_id": key[0],
                "acceptance_criterion_id": key[1],
                "traceability_status": "MAPPED",
                "traceability_reason": None,
                "expected_result": "之前通过的验收项继续通过",
                "actual_result": "本轮验收失败",
                "reproduction_steps": list(current.get("steps") or ["执行当前验收检查"]),
                "evidence_refs": list(current.get("evidence_refs") or []),
                "affected_scope": list(changed_files),
                "allowed_scope": list(changed_files),
                "forbidden_changes": [],
                "verification_commands": list(
                    current.get("verification_commands")
                    or [["internal", "recheck-acceptance-criterion"]]
                ),
                "blocking": True,
                "status": "OPEN",
                "route_to": "GENERATOR",
                "previous_passed_evaluation": old.get("evaluation_id"),
                "root_cause_key": (
                    f"regression|{key[0]}|{key[1]}|"
                    + ",".join(sorted(changed_files))
                ),
            }
        )
        sequence += 1
    return regressions


def update_routing_disagreements(
    prior: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    generator_response: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {
        item["issue_id"]: deepcopy(item)
        for item in prior
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }
    response_map = {
        item.get("issue_id"): item
        for item in generator_response.get("issue_responses", [])
        if isinstance(item, dict)
    }
    for issue in issues:
        response = response_map.get(issue.get("issue_id"))
        requested = response.get("requested_route") if response else None
        if requested and requested != issue.get("route_to"):
            record = by_id.get(
                issue["issue_id"],
                {
                    "issue_id": issue["issue_id"],
                    "evaluator_route": issue.get("route_to"),
                    "generator_requested_route": requested,
                    "disagreement_count": 0,
                },
            )
            if (
                record.get("evaluator_route") == issue.get("route_to")
                and record.get("generator_requested_route") == requested
            ):
                record["disagreement_count"] += 1
            else:
                record.update(
                    evaluator_route=issue.get("route_to"),
                    generator_requested_route=requested,
                    disagreement_count=1,
                )
            by_id[issue["issue_id"]] = record
    return sorted(by_id.values(), key=lambda item: item["issue_id"])


def decide_early_escalation(
    metrics_history: list[dict[str, Any]],
    repeat_history: list[dict[str, Any]],
    routing_disagreements: list[dict[str, Any]],
    *,
    rules: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    policy = dict(DEFAULT_ESCALATION_RULES)
    if rules:
        policy.update(rules)
    for item in routing_disagreements:
        if item.get("disagreement_count", 0) >= policy["routing_disagreement_rounds"]:
            return {
                "reason": "routing_disagreement_threshold",
                "target": "PLANNER",
                "status": "PLANNING",
                "issue_ids": [item["issue_id"]],
            }
    failed_claims = [
        item["issue_id"]
        for item in repeat_history
        if item.get("failed_fix_claim_count", 0)
        >= policy["repeated_failed_fix_claim"]
    ]
    if failed_claims:
        return {
            "reason": "repeated_failed_fix_claim_threshold",
            "target": "USER",
            "status": "WAITING_FOR_USER",
            "issue_ids": failed_claims,
        }
    repeated = [
        item["issue_id"]
        for item in repeat_history
        if item.get("repeat_count", 0) >= policy["repeated_same_issue"]
    ]
    if repeated:
        return {
            "reason": "repeated_same_issue_threshold",
            "target": "USER",
            "status": "WAITING_FOR_USER",
            "issue_ids": repeated,
        }
    regression_tail = 0
    no_progress_tail = 0
    for metrics in reversed(metrics_history):
        if metrics.get("new_regressions", 0) > 0:
            regression_tail += 1
        else:
            break
    for metrics in reversed(metrics_history):
        if metrics.get("progress_status") in {"STALLED", "STABLE"}:
            no_progress_tail += 1
        else:
            break
    if regression_tail >= policy["consecutive_regression_rounds"]:
        return {
            "reason": "consecutive_regression_threshold",
            "target": "USER",
            "status": "WAITING_FOR_USER",
            "issue_ids": [],
        }
    if no_progress_tail >= policy["no_progress_rounds"]:
        return {
            "reason": "no_progress_threshold",
            "target": "USER",
            "status": "WAITING_FOR_USER",
            "issue_ids": [],
        }
    return None


def apply_controlled_retry(
    state: dict[str, Any],
    issue_package: dict[str, Any],
    *,
    escalation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """唯一治理入口：只有发往 Generator 的可返工 FAIL 增加计数。"""
    if state.get("status") != "EVALUATING" or state.get("next_role") != "evaluator":
        raise ProjectStateError("受控重试只能由 EVALUATING/evaluator 提交")
    updated = deepcopy(state)
    result = issue_package.get("result")
    target = issue_package.get("return_to")
    if result == "PASS":
        updated.update(status="ACCEPTED", next_role=None, blocked_reason=None)
        return updated
    if result == "BLOCKED" or target == "SYSTEM_OR_USER":
        updated.update(
            status="BLOCKED",
            next_role=None,
            blocked_reason="evaluation_environment_blocked",
        )
        return updated
    if escalation:
        next_role = "planner" if escalation["target"] == "PLANNER" else None
        updated.update(
            status=escalation["status"],
            next_role=next_role,
            escalation_record=escalation,
            blocked_reason=escalation["reason"],
            automatic_retry_allowed=False,
        )
        return updated
    if target == "PLANNER":
        updated.update(status="PLANNING", next_role="planner", blocked_reason=None)
        return updated
    if target == "USER":
        updated.update(
            status="WAITING_FOR_USER",
            next_role=None,
            blocked_reason="requirement_ambiguity",
        )
        return updated
    if target != "GENERATOR":
        raise ProjectStateError("FAIL 的 return_to 不属于合法自动路由")
    iteration = updated.get("current_iteration", 0)
    if not isinstance(iteration, int) or not 0 <= iteration <= MAXIMUM_AUTOMATIC_ITERATIONS:
        raise ProjectStateError("current_iteration 无效")
    if iteration >= MAXIMUM_AUTOMATIC_ITERATIONS - 1:
        updated.update(
            current_iteration=MAXIMUM_AUTOMATIC_ITERATIONS,
            status="WAITING_FOR_USER",
            next_role=None,
            blocked_reason="maximum_iterations_reached",
            automatic_retry_allowed=False,
        )
        return updated
    updated.update(
        current_iteration=iteration + 1,
        status="IMPLEMENTING",
        next_role="generator",
        blocked_reason=None,
        automatic_retry_allowed=True,
    )
    return updated


def start_new_plan_iteration_sequence(
    state: dict[str, Any],
    *,
    new_approved_plan: str,
    new_plan_approval_record: str,
) -> dict[str, Any]:
    """只有新的正式 Plan 批准链可开启新序列并重置轮次。"""
    if state.get("status") != "APPROVED_FOR_IMPLEMENTATION":
        raise ProjectStateError("只有重新批准后的正式实施状态可开启新迭代序列")
    if (
        not isinstance(new_approved_plan, str)
        or new_approved_plan == state.get("approved_plan")
        or not isinstance(new_plan_approval_record, str)
        or new_plan_approval_record == state.get("plan_approval_record")
    ):
        raise ProjectStateError("必须提供不同的新 Plan 与新批准记录")
    updated = deepcopy(state)
    updated.update(
        approved_plan=new_approved_plan,
        active_plan=new_approved_plan,
        plan_approval_record=new_plan_approval_record,
        current_iteration=0,
        iteration_sequence=int(state.get("iteration_sequence", 1)) + 1,
        automatic_retry_allowed=True,
        blocked_reason=None,
    )
    return updated


def build_decision_summary(
    state: dict[str, Any],
    packages: list[dict[str, Any]],
    metrics_history: list[dict[str, Any]],
    repeat_history: list[dict[str, Any]],
    generator_responses: list[dict[str, Any]],
    recheck_records: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    current = packages[-1] if packages else {"issues": []}
    all_issues = [
        item
        for package in packages
        for item in package.get("issues", [])
        if isinstance(item, dict)
    ]
    resolved = sorted(
        {item["issue_id"] for item in all_issues if item.get("status") == "RESOLVED"}
    )
    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": state.get("project_id"),
        "iteration_sequence": state.get("iteration_sequence", 1),
        "current_iteration": state.get("current_iteration", 0),
        "reason": reason,
        "remaining_issues": [
            item for item in current.get("issues", []) if item.get("status") in {"OPEN", "REOPENED"}
        ],
        "repeated_issues": [
            item for item in repeat_history if item.get("repeat_count", 0) > 1
        ],
        "resolved_issue_ids": resolved,
        "new_regressions": [
            item for item in all_issues if item.get("category") == "regression"
        ],
        "trend_history": deepcopy(metrics_history),
        "generator_fix_history": deepcopy(generator_responses),
        "evaluator_recheck_history": deepcopy(recheck_records),
        "failure_causes": sorted(
            {
                item.get("root_cause_key") or issue_signature(item)
                for item in all_issues
                if item.get("status") in {"OPEN", "REOPENED"}
            }
        ),
        "options": [
            {
                "id": "revise_plan",
                "risk": "需要重新完成产品/Plan 批准链后才能开启新迭代序列",
            },
            {
                "id": "provide_clarification",
                "risk": "若不改变正式范围，只解决歧义，保留当前历史计数",
            },
            {
                "id": "stop",
                "risk": "当前未解决 Issue 将保留，项目不能认定为 ACCEPTED",
            },
        ],
    }


def write_decision_summary(
    project_root: str | Path,
    summary: dict[str, Any],
) -> Path:
    root = Path(project_root).resolve()
    directory = root / "evaluation" / "decisions"
    directory.mkdir(parents=True, exist_ok=True)
    existing_numbers = [
        int(path.stem.rsplit("-", 1)[-1])
        for path in directory.glob("decision-summary-*.yaml")
        if path.stem.rsplit("-", 1)[-1].isdigit()
    ]
    number = max(existing_numbers, default=0) + 1
    target = directory / f"decision-summary-{number:03d}.yaml"
    if target.exists():
        raise ProjectStateError("决策摘要已存在，禁止覆盖")
    temporary = target.with_suffix(".yaml.tmp")
    temporary.write_text(serialize_project_state(summary), encoding="utf-8")
    temporary.replace(target)
    return target
