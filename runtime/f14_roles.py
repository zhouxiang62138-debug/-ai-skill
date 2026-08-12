"""F14-F 各 Role/Phase 的最小完整 Context 闭包。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.errors import RuntimeValidationError


ROLE_PHASES: dict[str, tuple[str, ...]] = {
    "generator": ("rework", "resume", "initial_implementation", "change_request"),
    "planner": ("planning_revision", "change_impact", "product_revision"),
    "evaluator": ("evaluation", "regression"),
}

MANDATORY_SCOPE: dict[tuple[str, str], tuple[str, ...]] = {
    ("generator", "rework"): (
        "project_state_summary", "role", "task", "current_revision", "approved_scope",
        "evaluation_issue", "acceptance_criteria", "requirements", "global_constraints",
        "security_constraints", "privacy_constraints", "data_constraints",
        "compatibility_constraints", "regulatory_constraints", "approved_plan_task",
        "role_permission_boundary",
    ),
    ("generator", "resume"): (
        "project_state_summary", "role", "task", "current_revision", "approved_scope",
        "evaluation_issue", "acceptance_criteria", "requirements", "global_constraints",
        "security_constraints", "privacy_constraints", "data_constraints",
        "compatibility_constraints", "regulatory_constraints", "approved_plan_task",
        "role_permission_boundary",
    ),
    ("generator", "initial_implementation"): (
        "approved_plan", "product_spec", "requirements", "acceptance_criteria",
        "global_constraints", "architecture_data_contract", "relevant_existing_code",
        "relevant_tests", "approved_reference_binding",
    ),
    ("generator", "change_request"): (
        "approved_change_items", "affected_original_requirements", "affected_acceptance_criteria",
        "affected_code", "regression_boundary", "global_constraints",
    ),
    ("planner", "planning_revision"): (
        "user_facts", "unanswered_conflicts", "research_findings", "product_decisions",
        "approvals", "design_selection", "current_scope",
    ),
    ("planner", "change_impact"): (
        "user_facts", "current_scope", "approved_change_request", "affected_requirements",
        "affected_acceptance_criteria", "current_approvals", "current_revision",
    ),
    ("planner", "product_revision"): (
        "user_facts", "current_scope", "current_product_decisions", "approvals",
        "design_selection", "active_requirements",
    ),
    ("evaluator", "evaluation"): (
        "independent_mandatory_coverage", "approved_scope", "relevant_requirements",
        "relevant_acceptance_criteria", "current_revision", "current_code_snapshot",
        "mandatory_regression", "broader_regression_boundary", "independent_evidence",
        "evaluation_profile",
    ),
    ("evaluator", "regression"): (
        "independent_mandatory_coverage", "approved_scope", "relevant_requirements",
        "relevant_acceptance_criteria", "current_revision", "current_code_snapshot",
        "mandatory_regression", "broader_regression_boundary", "independent_evidence",
        "evaluation_profile",
    ),
}

TASK_RELEVANT_SCOPE: dict[tuple[str, str], tuple[str, ...]] = {
    ("generator", "rework"): (
        "changed_files", "issue_related_code", "related_tests", "latest_generator_response",
        "runtime_evidence", "browser_evidence",
    ),
    ("generator", "resume"): (
        "changed_files", "issue_related_code", "related_tests", "latest_generator_response",
        "runtime_evidence", "browser_evidence",
    ),
    ("generator", "initial_implementation"): (
        "relevant_existing_code", "relevant_tests", "approved_reference_binding",
    ),
    ("generator", "change_request"): (
        "affected_code", "affected_tests", "original_regression_evidence",
    ),
    ("planner", "planning_revision"): (
        "relevant_research", "current_product_decisions", "current_approvals",
    ),
    ("planner", "change_impact"): (
        "affected_code_map", "affected_evidence", "regression_boundary",
    ),
    ("planner", "product_revision"): (
        "relevant_research", "current_product_decisions", "design_preview_evidence",
    ),
    ("evaluator", "evaluation"): (
        "changed_files", "issue_map", "related_tests", "browser_evidence",
        "runtime_evidence",
    ),
    ("evaluator", "regression"): (
        "changed_files", "issue_map", "related_tests", "browser_evidence",
        "runtime_evidence",
    ),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class RoleContextScope:
    role: str
    phase: str
    mandatory: tuple[str, ...]
    task_relevant: tuple[str, ...]
    delivered: tuple[str, ...]
    on_demand: tuple[str, ...]
    missing_mandatory: tuple[str, ...]
    unknown: tuple[str, ...]
    scope_hash: str

    @property
    def complete(self) -> bool:
        return not self.missing_mandatory and not self.unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "phase": self.phase,
            "mandatory": list(self.mandatory),
            "task_relevant": list(self.task_relevant),
            "delivered": list(self.delivered),
            "on_demand": list(self.on_demand),
            "missing_mandatory": list(self.missing_mandatory),
            "unknown": list(self.unknown),
            "scope_hash": self.scope_hash,
        }


class RoleContextScopeBuilder:
    """按批准的 Role/Phase 闭包选取 Context，不读取无关历史。"""

    def build(
        self,
        *,
        role: str,
        phase: str,
        available: Mapping[str, Any],
        requested_on_demand: tuple[str, ...] = (),
    ) -> RoleContextScope:
        if (role, phase) not in MANDATORY_SCOPE:
            raise RuntimeValidationError("F14_ROLE_PHASE_SCOPE_INVALID")
        if not isinstance(available, Mapping):
            raise RuntimeValidationError("F14_ROLE_CONTEXT_INPUT_INVALID")
        mandatory = MANDATORY_SCOPE[(role, phase)]
        task_relevant = TASK_RELEVANT_SCOPE.get((role, phase), ())
        delivered = tuple(item for item in mandatory if item in available)
        delivered += tuple(item for item in task_relevant if item in available and item not in delivered)
        missing = tuple(item for item in mandatory if item not in available)
        known = set(mandatory) | set(task_relevant)
        unknown = tuple(sorted(
            item for item in available
            if item not in known and item not in requested_on_demand
        ))
        on_demand = tuple(sorted({item for item in requested_on_demand if item in available}))
        payload = {
            "role": role,
            "phase": phase,
            "mandatory": mandatory,
            "task_relevant": task_relevant,
            "delivered": delivered,
            "on_demand": on_demand,
            "missing_mandatory": missing,
            "unknown": unknown,
        }
        return RoleContextScope(
            role=role,
            phase=phase,
            mandatory=mandatory,
            task_relevant=task_relevant,
            delivered=delivered,
            on_demand=on_demand,
            missing_mandatory=missing,
            unknown=unknown,
            scope_hash=_hash(payload),
        )


__all__ = [
    "MANDATORY_SCOPE",
    "ROLE_PHASES",
    "TASK_RELEVANT_SCOPE",
    "RoleContextScope",
    "RoleContextScopeBuilder",
]
