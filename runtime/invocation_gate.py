"""F14-F6 确定性 Invocation Gate。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from runtime.errors import RuntimeValidationError


PYTHON_ONLY = "PYTHON_ONLY"
LLM_REQUIRED = "LLM_REQUIRED"
BLOCKED = "BLOCKED"
INVOCATION_RESULTS = frozenset({PYTHON_ONLY, LLM_REQUIRED, BLOCKED})

DETERMINISTIC_TASKS = frozenset(
    {
        "state_validation", "schema_validation", "revision", "cas_precheck",
        "approval_chain_validation", "artifact_lookup", "hash", "diff",
        "test_parsing", "browser_deterministic_evidence_parsing",
        "dependency_validation", "context_coverage", "workflow_transition",
        "cache_validation", "baseline_validation", "manifest_validation",
    }
)
SEMANTIC_TASKS = frozenset(
    {
        "semantic_understanding", "ambiguity", "creative_judgment", "implementation",
        "architecture_reasoning", "evaluation_judgment", "product_decision",
        "bug_diagnosis", "issue_judgment", "design_review", "requirement_judgment",
        "generator_output", "planner_output", "evaluator_verdict",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_refs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise RuntimeValidationError("F14_INVOCATION_EVIDENCE_REFS_INVALID")
    if any(not isinstance(item, str) or not item for item in value):
        raise RuntimeValidationError("F14_INVOCATION_EVIDENCE_REFS_INVALID")
    return tuple(sorted(set(value)))


@dataclass(frozen=True)
class InvocationGateRequest:
    """Invocation Gate 的结构化输入，不包含 Prompt 或私有推理。"""

    role: str
    phase: str
    task_kind: str
    task: str = ""
    project_revision: int = 0
    policy_hash: str = ""
    evidence_refs: tuple[str, ...] = ()
    input_payload: Mapping[str, Any] = field(default_factory=dict)
    semantic_result: Any = None
    previous_verdict: Any = None
    evaluator_fresh: bool = True
    coverage_valid: bool = True
    authority_valid: bool = True
    state_valid: bool = True
    security_valid: bool = True

    def __post_init__(self) -> None:
        for name in ("role", "phase", "task_kind"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise RuntimeValidationError("F14_INVOCATION_REQUEST_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("F14_INVOCATION_REQUEST_INVALID")
        if not isinstance(self.input_payload, Mapping):
            raise RuntimeValidationError("F14_INVOCATION_REQUEST_INVALID")
        object.__setattr__(self, "evidence_refs", _safe_refs(self.evidence_refs))
        for name in (
            "evaluator_fresh", "coverage_valid", "authority_valid", "state_valid", "security_valid"
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeValidationError("F14_INVOCATION_REQUEST_INVALID")

    @property
    def input_hash(self) -> str:
        return _hash(
            {
                "role": self.role,
                "phase": self.phase,
                "task_kind": self.task_kind,
                "task": self.task,
                "project_revision": self.project_revision,
                "policy_hash": self.policy_hash,
                "evidence_refs": self.evidence_refs,
                "input_payload": self.input_payload,
            }
        )


@dataclass(frozen=True)
class InvocationGateDecision:
    result: str
    reason: str
    evidence_refs: tuple[str, ...]
    input_hash: str
    project_revision: int
    policy_hash: str
    real_model_request: bool
    lifecycle_record_required: bool = True

    def __post_init__(self) -> None:
        if self.result not in INVOCATION_RESULTS:
            raise RuntimeValidationError("F14_INVOCATION_RESULT_INVALID")
        if not isinstance(self.evidence_refs, tuple):
            raise RuntimeValidationError("F14_INVOCATION_EVIDENCE_REFS_INVALID")
        if len(self.input_hash) != 64 or len(self.policy_hash) != 64:
            raise RuntimeValidationError("F14_INVOCATION_HASH_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_gate": {
                "result": self.result,
                "reason": self.reason,
                "evidence_refs": list(self.evidence_refs),
                "input_hash": self.input_hash,
                "project_revision": self.project_revision,
                "policy_hash": self.policy_hash,
                "real_model_request": self.real_model_request,
                "lifecycle_record_required": self.lifecycle_record_required,
            }
        }


@dataclass(frozen=True)
class InvocationExecution:
    decision: InvocationGateDecision
    value: Any = None
    lifecycle: Mapping[str, Any] = field(default_factory=dict)


class InvocationGate:
    """只把可完全复现的任务分到 Python；语义任务默认保守地要求 LLM。"""

    def __init__(self, *, enabled: bool = True, policy_hash: str | None = None) -> None:
        self.enabled = bool(enabled)
        self.policy_hash = policy_hash if policy_hash and len(policy_hash) == 64 else _hash(
            {"policy": "f14-deterministic-invocation-gate-v1", "enabled": self.enabled}
        )
        self.lifecycle_records: list[dict[str, Any]] = []

    @classmethod
    def from_f14_config(cls, path: str | None = None) -> "InvocationGate":
        """从正式 F14 配置读取开关；资格不足时不会自行打开 Gate。"""

        from runtime.f14_control import F14FeatureFlags

        flags = F14FeatureFlags.load(path)
        return cls(
            enabled=(
                flags.invocation_gate_enabled
                and flags.invocation_gate_qualified()
                and flags.rollout.effective_delivery_enabled
            )
        )

    @staticmethod
    def _infer_task_kind(task_kind: str, task: str) -> str:
        normalized = task_kind.strip().casefold()
        if normalized in DETERMINISTIC_TASKS or normalized in SEMANTIC_TASKS:
            return normalized
        text = f"{normalized} {task.casefold()}"
        deterministic_tokens = (
            "hash", "schema", "revision", "cas", "artifact lookup", "test parsing",
            "browser evidence parsing", "dependency validation", "context coverage",
            "workflow transition", "cache validation", "diff",
        )
        if any(token in text for token in deterministic_tokens):
            return "deterministic_unknown"
        return "semantic_unknown"

    def evaluate(self, request: InvocationGateRequest) -> InvocationGateDecision:
        if not isinstance(request, InvocationGateRequest):
            raise RuntimeValidationError("F14_INVOCATION_REQUEST_INVALID")
        if not self.enabled:
            result = LLM_REQUIRED
            reason = "invocation_gate_disabled"
        elif not all((request.coverage_valid, request.authority_valid, request.state_valid, request.security_valid)):
            result = BLOCKED
            reason = "coverage_authority_state_or_security_invalid"
        elif request.role == "evaluator" and not request.evaluator_fresh:
            result = BLOCKED
            reason = "evaluator_fresh_invocation_required"
        elif request.semantic_result is not None or request.previous_verdict is not None:
            result = LLM_REQUIRED
            reason = "semantic_result_reuse_forbidden"
        else:
            kind = self._infer_task_kind(request.task_kind, request.task)
            if kind in DETERMINISTIC_TASKS:
                result = PYTHON_ONLY
                reason = "deterministic_reproducible_task"
            elif kind == "deterministic_unknown":
                result = LLM_REQUIRED
                reason = "deterministic_task_not_proven"
            else:
                result = LLM_REQUIRED
                reason = "semantic_judgment_required"
        return InvocationGateDecision(
            result=result,
            reason=reason,
            evidence_refs=request.evidence_refs,
            input_hash=request.input_hash,
            project_revision=request.project_revision,
            policy_hash=self.policy_hash,
            real_model_request=result == LLM_REQUIRED,
        )

    decide = evaluate

    def execute(
        self,
        request: InvocationGateRequest,
        *,
        python_handler: Callable[[InvocationGateRequest], Any] | None = None,
        llm_handler: Callable[[InvocationGateRequest], Any] | None = None,
    ) -> InvocationExecution:
        decision = self.evaluate(request)
        lifecycle = {
            "record_type": "invocation_lifecycle",
            "execution_type": "python_only" if decision.result == PYTHON_ONLY else "llm",
            "real_model_request": decision.real_model_request,
            "result": decision.result,
            "input_hash": decision.input_hash,
            "project_revision": decision.project_revision,
            "policy_hash": decision.policy_hash,
            "role": request.role,
            "phase": request.phase,
        }
        self.lifecycle_records.append(dict(lifecycle))
        if decision.result == BLOCKED:
            return InvocationExecution(decision, None, lifecycle)
        if decision.result == PYTHON_ONLY:
            if python_handler is None:
                raise RuntimeValidationError("F14_PYTHON_HANDLER_REQUIRED")
            return InvocationExecution(decision, python_handler(request), lifecycle)
        if llm_handler is None:
            raise RuntimeValidationError("F14_LLM_HANDLER_REQUIRED")
        return InvocationExecution(decision, llm_handler(request), lifecycle)


__all__ = [
    "BLOCKED",
    "DETERMINISTIC_TASKS",
    "INVOCATION_RESULTS",
    "LLM_REQUIRED",
    "PYTHON_ONLY",
    "InvocationExecution",
    "InvocationGate",
    "InvocationGateDecision",
    "InvocationGateRequest",
    "SEMANTIC_TASKS",
]
