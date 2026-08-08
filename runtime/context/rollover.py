"""Stage 6 Runtime Context Rollover 与 Fresh Model Invocation 服务。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from runtime.errors import RuntimeValidationError
from runtime.session_store import SessionStore
from scripts.project_state import ProjectStateError, parse_project_yaml

from .builder import ContextBuilder
from .models import ContextBuildRequest, ContextPackage


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "context.yaml"
HANDOFF_FIELDS = {
    "completed",
    "current_state",
    "files_changed",
    "verification_completed",
    "open_issues",
    "known_failures",
    "next_actions",
    "important_decisions",
    "do_not_repeat",
    "references",
}
SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|private[_-]?key|authorization|bearer\s+)"
)


@dataclass(frozen=True)
class RolloverPolicy:
    enabled: bool = True
    context_budget_percent: int = 85
    max_tool_calls: int = 40
    max_compactions: int = 3
    max_elapsed_seconds: int = 1800
    on_phase_transition: bool = True
    on_role_transition: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise RuntimeValidationError("ROLLOVER_POLICY_INVALID")
        if not isinstance(self.context_budget_percent, int) or not 1 <= self.context_budget_percent <= 100:
            raise RuntimeValidationError("ROLLOVER_POLICY_INVALID")
        for value in (self.max_tool_calls, self.max_compactions, self.max_elapsed_seconds):
            if not isinstance(value, int) or value < 1:
                raise RuntimeValidationError("ROLLOVER_POLICY_INVALID")
        if not isinstance(self.on_phase_transition, bool) or not isinstance(self.on_role_transition, bool):
            raise RuntimeValidationError("ROLLOVER_POLICY_INVALID")


@dataclass(frozen=True)
class InvocationStats:
    """由 Runtime 记录的 Invocation 统计，不接受模型自报的上下文长度。"""

    tool_call_count: int = 0
    compaction_count: int = 0
    elapsed_seconds: int = 0
    phase_transition: bool = False
    role_transition: bool = False

    def __post_init__(self) -> None:
        for value in (self.tool_call_count, self.compaction_count, self.elapsed_seconds):
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError("ROLLOVER_STATS_INVALID")
        if not isinstance(self.phase_transition, bool) or not isinstance(self.role_transition, bool):
            raise RuntimeValidationError("ROLLOVER_STATS_INVALID")


@dataclass(frozen=True)
class RolloverDecision:
    required: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RolloverHandoff:
    """只保存可审计摘要和引用，不保存完整 Context 正文。"""

    completed: tuple[str, ...]
    current_state: tuple[str, ...]
    files_changed: tuple[str, ...]
    verification_completed: tuple[str, ...]
    open_issues: tuple[str, ...]
    known_failures: tuple[str, ...]
    next_actions: tuple[str, ...]
    important_decisions: tuple[str, ...]
    do_not_repeat: tuple[str, ...]
    references: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in HANDOFF_FIELDS:
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise RuntimeValidationError("ROLLOVER_HANDOFF_FIELDS_INVALID")
            if len(values) > 64 or any(len(item) > 2048 for item in values):
                raise RuntimeValidationError("ROLLOVER_HANDOFF_TOO_LARGE")
            if any(SECRET_PATTERN.search(item) for item in values):
                raise RuntimeValidationError("ROLLOVER_HANDOFF_SECRET_FORBIDDEN")
        if not self.current_state or not self.next_actions or not self.references:
            raise RuntimeValidationError("ROLLOVER_HANDOFF_INCOMPLETE")
        for reference in self.references:
            normalized = reference.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or "../" in f"{normalized}/":
                raise RuntimeValidationError("ROLLOVER_HANDOFF_REFERENCE_INVALID")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RolloverHandoff":
        if not isinstance(value, Mapping) or set(value) != HANDOFF_FIELDS:
            raise RuntimeValidationError("ROLLOVER_HANDOFF_FIELDS_INVALID")
        converted: dict[str, tuple[str, ...]] = {}
        for field_name in HANDOFF_FIELDS:
            raw = value[field_name]
            if not isinstance(raw, (list, tuple)):
                raise RuntimeValidationError("ROLLOVER_HANDOFF_FIELDS_INVALID")
            converted[field_name] = tuple(raw)
        return cls(**converted)

    def to_dict(self) -> dict[str, list[str]]:
        return {field_name: list(getattr(self, field_name)) for field_name in HANDOFF_FIELDS}


@dataclass(frozen=True)
class FreshInvocationContext:
    invocation: dict[str, Any]
    handoff: dict[str, Any]
    context: ContextPackage


def load_rollover_policy(path: str | Path | None = None) -> RolloverPolicy:
    """读取 context.yaml 中的 Runtime rollover 策略。"""

    policy_path = Path(path or DEFAULT_POLICY_PATH)
    try:
        value = parse_project_yaml(policy_path.read_text(encoding="utf-8"))
    except (OSError, ProjectStateError) as exc:
        raise RuntimeValidationError("ROLLOVER_POLICY_UNAVAILABLE") from exc
    rollover = value.get("rollover") if isinstance(value, dict) else None
    if not isinstance(rollover, dict):
        raise RuntimeValidationError("ROLLOVER_POLICY_INVALID")
    try:
        return RolloverPolicy(**rollover)
    except TypeError as exc:
        raise RuntimeValidationError("ROLLOVER_POLICY_INVALID") from exc


def evaluate_rollover(
    context: ContextPackage,
    stats: InvocationStats,
    *,
    policy: RolloverPolicy | None = None,
) -> RolloverDecision:
    """只根据 Runtime 可观测状态判断是否需要新 Invocation。"""

    current_policy = policy or load_rollover_policy()
    if not current_policy.enabled:
        return RolloverDecision(False, ())
    reasons: list[str] = []
    if context.budget_limit > 0 and context.budget_used * 100 >= context.budget_limit * current_policy.context_budget_percent:
        reasons.append("context_budget_threshold")
    if stats.tool_call_count >= current_policy.max_tool_calls:
        reasons.append("tool_call_threshold")
    if stats.compaction_count >= current_policy.max_compactions:
        reasons.append("compaction_threshold")
    if stats.elapsed_seconds >= current_policy.max_elapsed_seconds:
        reasons.append("long_running_invocation")
    if current_policy.on_phase_transition and stats.phase_transition:
        reasons.append("phase_transition")
    if current_policy.on_role_transition and stats.role_transition:
        reasons.append("role_transition")
    return RolloverDecision(bool(reasons), tuple(dict.fromkeys(reasons)))


class ContextRolloverService:
    """连接 F13 Context Builder 与 F10 Durable Session 的确定性服务。"""

    def __init__(
        self,
        store: SessionStore,
        *,
        context_builder: ContextBuilder | None = None,
        rollover_policy: RolloverPolicy | None = None,
    ) -> None:
        self._store = store
        self._builder = context_builder or ContextBuilder(store)
        self._policy = rollover_policy or load_rollover_policy()

    def start_invocation(self, context: ContextPackage, *, idempotency_key: str) -> dict[str, Any]:
        return self._store.create_model_invocation(
            context.session_id,
            context.run_id,
            context.role,
            context.context_id,
            idempotency_key=idempotency_key,
        )

    def rollover(
        self,
        context: ContextPackage,
        stats: InvocationStats,
        handoff: Mapping[str, Any],
        *,
        invocation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        decision = evaluate_rollover(context, stats, policy=self._policy)
        if not decision.required:
            raise RuntimeValidationError("ROLLOVER_NOT_REQUIRED")
        validated = RolloverHandoff.from_mapping(handoff)
        if context.context_id not in validated.references:
            raise RuntimeValidationError("ROLLOVER_CONTEXT_REFERENCE_MISSING")
        return self._store.append_rollover_handoff(
            context.session_id,
            invocation_id,
            context.role,
            validated.to_dict(),
            reason={"reason_codes": list(decision.reason_codes)},
            idempotency_key=idempotency_key,
        )

    def start_fresh_invocation(
        self,
        session_id: str,
        previous_invocation_id: str,
        run_id: str,
        *,
        additional_references: tuple[str, ...] = (),
        idempotency_key: str,
    ) -> FreshInvocationContext:
        """要求旧 Invocation 已有 Handoff，再用 F13 构建全新 Context。"""

        previous = self._store.get_model_invocation(session_id, previous_invocation_id)
        if previous["status"] != "ROLLED_OVER" or not previous.get("handoff_id"):
            raise RuntimeValidationError("ROLLOVER_HANDOFF_MISSING")
        handoff_record = self._store.get_rollover_handoff(session_id, previous["handoff_id"])
        role = str(previous["role"])
        context = self._builder.build(
            ContextBuildRequest(session_id, run_id, role, tuple(additional_references))
        )
        invocation = self._store.create_model_invocation(
            session_id,
            run_id,
            role,
            context.context_id,
            idempotency_key=idempotency_key,
            previous_invocation_id=previous_invocation_id,
        )
        return FreshInvocationContext(
            invocation=invocation,
            handoff=handoff_record["handoff"],
            context=context,
        )


__all__ = [
    "ContextRolloverService",
    "FreshInvocationContext",
    "InvocationStats",
    "RolloverDecision",
    "RolloverHandoff",
    "RolloverPolicy",
    "evaluate_rollover",
    "load_rollover_policy",
]
