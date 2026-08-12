"""F14-D Shadow/Controlled Context Escalation。

本模块只提供受控的上下文扩展旁路：模型可以提出结构化请求，Runtime
负责授权、解析索引、校验路径和 freshness，再把扩展结果作为独立证据返回。
正式 F13 Context、默认 Selective Context 和 LLM Invocation 都不在这里改变。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping

from runtime.deterministic.store import DerivedRuntimeStore
from runtime.deterministic.telemetry import RuntimeTelemetry
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.event_types import ActorType, EventType
from runtime.policy import CapabilityPolicy
from runtime.execution.path_policy import ExecutionPathPolicy, PathAccessDenied
from runtime.session_store import SessionStore, stable_id
from scripts.project_state import load_project_state
from runtime.project_revision import runtime_projection


ESCALATION_LEVELS = frozenset({"L1", "L2", "L3"})
ESCALATION_DECISIONS = frozenset({"APPROVED", "DENIED", "UNAVAILABLE"})
ESCALATION_STATUSES = frozenset({"DELIVERED", "DENIED", "FALLBACK_F13"})
URGENCY_VALUES = frozenset({"LOW", "NORMAL", "HIGH"})
ROLE_VALUES = frozenset({"planner", "generator", "evaluator"})
AUTHORITY_VALUES = frozenset(
    {
        "USER_EXPLICIT",
        "APPROVED_REQUIREMENT",
        "APPROVED_PRODUCT_SPEC",
        "APPROVED_PLAN",
        "APPROVED_CHANGE_SCOPE",
        "RUNTIME_EVIDENCE",
        "GENERATED_ARTIFACT",
        "HISTORICAL",
    }
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|secret|password|private[_-]?key)\s*[:=]"
    r"|(?:bearer\s+|ghp_|github_pat_|sk-[A-Za-z0-9])"
)
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _audit_ref(value: str) -> str:
    """事件只保存稳定引用，避免调用方 ID 触发敏感词扫描或泄露原值。"""

    return "ref:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _audit_reason(value: str) -> str:
    """敏感词型拒绝原因只记录稳定摘要，保留可比对性而不泄露原文。"""

    if _SECRET_PATTERN.search(value) or re.search(
        r"(?i)(authorization|api[_-]?key|access[_-]?token|secret|password)", value
    ):
        return _audit_ref(value)
    return value[:128]


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    return value


def _safe_id(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
    ):
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    return value


def _safe_text(value: str, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    if _SECRET_PATTERN.search(value):
        raise RuntimeValidationError(f"{name.upper()}_SECRET_FORBIDDEN")
    return value


@dataclass(frozen=True)
class ContextRequest:
    """模型或 Role 提出的结构化扩展请求，不携带任意文件路径。"""

    request_id: str
    session_id: str
    run_id: str
    role: str
    project_revision: int
    current_context_hash: str
    missing_dependency_refs: tuple[str, ...]
    desired_source_kinds: tuple[str, ...]
    requested_level: str
    reason: str
    urgency: str = "NORMAL"
    requested_budget_bytes: int = 4096
    expansion_count: int = 0
    artifact_snapshot_id: str = ""
    dependency_snapshot_id: str = ""

    def __post_init__(self) -> None:
        _safe_id(self.request_id, "context_request_id")
        for name in ("session_id", "run_id"):
            _safe_text(getattr(self, name), name, 256)
        if self.role not in ROLE_VALUES:
            raise RuntimeValidationError("CONTEXT_REQUEST_ROLE_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("CONTEXT_REQUEST_REVISION_INVALID")
        _hash(self.current_context_hash, "context_request_hash")
        refs = tuple(self.missing_dependency_refs)
        kinds = tuple(self.desired_source_kinds)
        if not refs or any(not isinstance(item, str) for item in refs):
            raise RuntimeValidationError("CONTEXT_REQUEST_DEPENDENCY_REFS_INVALID")
        for item in refs:
            _safe_id(item, "context_dependency_ref")
        if not kinds or any(not isinstance(item, str) or not item for item in kinds):
            raise RuntimeValidationError("CONTEXT_REQUEST_SOURCE_KINDS_INVALID")
        if self.requested_level not in ESCALATION_LEVELS:
            raise RuntimeValidationError("CONTEXT_REQUEST_LEVEL_INVALID")
        _safe_text(self.reason, "context_request_reason", 512)
        if self.urgency not in URGENCY_VALUES:
            raise RuntimeValidationError("CONTEXT_REQUEST_URGENCY_INVALID")
        if not isinstance(self.requested_budget_bytes, int) or not 0 < self.requested_budget_bytes <= 1_048_576:
            raise RuntimeValidationError("CONTEXT_REQUEST_BUDGET_INVALID")
        if not isinstance(self.expansion_count, int) or self.expansion_count < 0:
            raise RuntimeValidationError("CONTEXT_REQUEST_COUNT_INVALID")
        if self.artifact_snapshot_id:
            _safe_id(self.artifact_snapshot_id, "context_artifact_snapshot_id")
        if self.dependency_snapshot_id:
            _safe_id(self.dependency_snapshot_id, "context_dependency_snapshot_id")
        object.__setattr__(self, "missing_dependency_refs", refs)
        object.__setattr__(self, "desired_source_kinds", kinds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "role": self.role,
            "project_revision": self.project_revision,
            "current_context_hash": self.current_context_hash,
            "missing_dependency_refs": list(self.missing_dependency_refs),
            "desired_source_kinds": list(self.desired_source_kinds),
            "requested_level": self.requested_level,
            "reason": self.reason,
            "urgency": self.urgency,
            "requested_budget_bytes": self.requested_budget_bytes,
            "expansion_count": self.expansion_count,
            "artifact_snapshot_id": self.artifact_snapshot_id,
            "dependency_snapshot_id": self.dependency_snapshot_id,
        }


@dataclass(frozen=True)
class ContextAuthorization:
    """Runtime 生成的授权结果；调用方不能伪造 resolved artifact 列表。"""

    authorization_id: str
    request_id: str
    decision: str
    reasons: tuple[str, ...]
    resolved_artifact_ids: tuple[str, ...]
    dependency_closure: tuple[str, ...]
    project_revision: int
    policy_hash: str
    authorization_hash: str

    def __post_init__(self) -> None:
        _safe_id(self.authorization_id, "context_authorization_id")
        _safe_id(self.request_id, "context_request_id")
        if self.decision not in ESCALATION_DECISIONS:
            raise RuntimeValidationError("CONTEXT_AUTHORIZATION_DECISION_INVALID")
        if self.project_revision < 0:
            raise RuntimeValidationError("CONTEXT_AUTHORIZATION_REVISION_INVALID")
        _hash(self.policy_hash, "context_policy_hash")
        _hash(self.authorization_hash, "context_authorization_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "request_id": self.request_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "resolved_artifact_ids": list(self.resolved_artifact_ids),
            "dependency_closure": list(self.dependency_closure),
            "project_revision": self.project_revision,
            "policy_hash": self.policy_hash,
            "authorization_hash": self.authorization_hash,
        }


@dataclass(frozen=True)
class ExpandedSource:
    """扩展交付的安全来源；L1 只交付 metadata，L2/L3 才交付校验后的内容。"""

    artifact_id: str
    kind: str
    locator: str
    content_hash: str
    level: str
    delivery_mode: str
    content: str | None = None

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "locator": self.locator,
            "content_hash": self.content_hash,
            "level": self.level,
            "delivery_mode": self.delivery_mode,
        }
        if include_content and self.content is not None:
            value["content"] = self.content
        return value


@dataclass(frozen=True)
class ContextExpansion:
    expansion_id: str
    request_id: str
    status: str
    level: str
    reasons: tuple[str, ...]
    sources: tuple[ExpandedSource, ...]
    delivered_bytes: int
    authorization_id: str

    def __post_init__(self) -> None:
        _safe_id(self.expansion_id, "context_expansion_id")
        _safe_id(self.request_id, "context_request_id")
        if self.status not in ESCALATION_STATUSES:
            raise RuntimeValidationError("CONTEXT_EXPANSION_STATUS_INVALID")
        if self.level not in ESCALATION_LEVELS:
            raise RuntimeValidationError("CONTEXT_EXPANSION_LEVEL_INVALID")
        if self.delivered_bytes < 0:
            raise RuntimeValidationError("CONTEXT_EXPANSION_BYTES_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expansion_id": self.expansion_id,
            "request_id": self.request_id,
            "status": self.status,
            "level": self.level,
            "reasons": list(self.reasons),
            "sources": [source.to_dict() for source in self.sources],
            "delivered_bytes": self.delivered_bytes,
            "authorization_id": self.authorization_id,
        }


@dataclass(frozen=True)
class ContextRecovery:
    recovery_id: str
    request_id: str
    status: str
    reason: str
    fallback_context_hash: str
    preserved_formal_context: bool

    def __post_init__(self) -> None:
        _safe_id(self.recovery_id, "context_recovery_id")
        _safe_id(self.request_id, "context_request_id")
        _safe_text(self.reason, "context_recovery_reason", 512)
        _hash(self.fallback_context_hash, "fallback_context_hash")
        if not self.preserved_formal_context:
            raise RuntimeValidationError("CONTEXT_RECOVERY_FORMAL_CONTEXT_LOST")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "request_id": self.request_id,
            "status": self.status,
            "reason": self.reason,
            "fallback_context_hash": self.fallback_context_hash,
            "preserved_formal_context": self.preserved_formal_context,
        }


@dataclass(frozen=True)
class ExpansionLoopResult:
    status: str
    formal_context: Any
    expansions: tuple[ContextExpansion, ...]
    recovery: ContextRecovery | None
    total_delivered_bytes: int
    llm_invocations: int = 0


class ContextEscalationRuntime:
    """F14-D Runtime 授权与受控扩展入口。"""

    def __init__(
        self,
        store: SessionStore,
        *,
        policy_hash: str,
        path_policy: ExecutionPathPolicy | None = None,
        capability_policy: CapabilityPolicy | None = None,
        max_expansions: int = 3,
        max_total_bytes: int = 1_048_576,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        _hash(policy_hash, "context_policy_hash")
        if not isinstance(max_expansions, int) or max_expansions <= 0:
            raise RuntimeValidationError("CONTEXT_MAX_EXPANSIONS_INVALID")
        if not isinstance(max_total_bytes, int) or max_total_bytes <= 0:
            raise RuntimeValidationError("CONTEXT_MAX_BYTES_INVALID")
        self.store = store
        self.policy_hash = policy_hash
        self.path_policy = path_policy or ExecutionPathPolicy()
        self.capability_policy = capability_policy or CapabilityPolicy()
        self.max_expansions = max_expansions
        self.max_total_bytes = max_total_bytes
        self.derived = DerivedRuntimeStore(store)
        self.telemetry = telemetry or RuntimeTelemetry()

    @staticmethod
    def _context_hash(context: Any) -> str:
        value = getattr(context, "context_hash", None)
        if value is None and isinstance(context, Mapping):
            value = context.get("context_hash")
        return _hash(value, "context_hash")

    def _runtime_state(self, request: ContextRequest) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        session = self.store.get_session(request.session_id)
        root = Path(session.project_root).resolve()
        state = load_project_state(root / "project.yaml")
        runtime = runtime_projection(state)
        if runtime.get("session_id") != request.session_id or state.get("project_id") != session.project_id:
            raise RuntimeValidationError("CONTEXT_ESCALATION_SESSION_PROJECT_MISMATCH")
        return root, state, runtime

    def record_request(self, request: ContextRequest) -> str:
        self.derived.write_context_request(
            session_id=request.session_id,
            request_id=request.request_id,
            request=request.to_dict(),
        )
        self.store.append_event(
            request.session_id,
            EventType.CONTEXT_REQUESTED,
            ActorType.ROLE,
            request.role,
            idempotency_key=f"context-request:{request.request_id}",
            correlation_id=request.request_id,
            payload={
                "request_ref": _audit_ref(request.request_id),
                "run_ref": _audit_ref(request.run_id),
                "role": request.role,
                "project_revision": request.project_revision,
                "requested_level": request.requested_level,
                "missing_dependency_count": len(request.missing_dependency_refs),
            },
        )
        self.telemetry.record_context_escalation(requests=1)
        self.telemetry.record_quality(context_expansion_requests=1)
        return request.request_id

    def _authorization(
        self,
        request: ContextRequest,
        decision: str,
        reasons: Iterable[str],
        resolved: Iterable[str],
        closure: Iterable[str],
        revision: int,
    ) -> ContextAuthorization:
        payload = {
            "request_id": request.request_id,
            "decision": decision,
            "reasons": sorted(set(reasons)),
            "resolved_artifact_ids": sorted(set(resolved)),
            "dependency_closure": sorted(set(closure)),
            "project_revision": revision,
            "policy_hash": self.policy_hash,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        authorization_id = stable_id("f14-context-auth", request.request_id, digest)
        authorization = ContextAuthorization(
            authorization_id=authorization_id,
            request_id=request.request_id,
            decision=decision,
            reasons=tuple(payload["reasons"]),
            resolved_artifact_ids=tuple(payload["resolved_artifact_ids"]),
            dependency_closure=tuple(payload["dependency_closure"]),
            project_revision=revision,
            policy_hash=self.policy_hash,
            authorization_hash=digest,
        )
        if decision == "APPROVED":
            self.telemetry.record_context_escalation(approved=1)
        elif decision == "DENIED":
            self.telemetry.record_context_escalation(denied=1)
            self.telemetry.record_quality(context_expansion_denied=1)
        else:
            self.telemetry.record_context_escalation(unavailable=1)
        self.derived.write_context_authorization(
            session_id=request.session_id,
            request_id=request.request_id,
            authorization_id=authorization.authorization_id,
            authorization=authorization.to_dict(),
            decision=decision,
        )
        self.store.append_event(
            request.session_id,
            EventType.CONTEXT_AUTHORIZED,
            ActorType.ORCHESTRATOR,
            "context-escalation-runtime",
            idempotency_key=f"context-authorized:{authorization.authorization_id}",
            correlation_id=request.request_id,
            payload={
                "request_ref": _audit_ref(request.request_id),
                "grant_ref": _audit_ref(authorization.authorization_id),
                "decision": decision,
                "reasons": list(authorization.reasons),
                "resolved_count": len(authorization.resolved_artifact_ids),
            },
        )
        return authorization

    def authorize(self, request: ContextRequest) -> ContextAuthorization:
        """只允许从已持久化的 Artifact/Dependency snapshot 解析来源。"""

        self.record_request(request)
        try:
            root, state, runtime = self._runtime_state(request)
            current_revision = int(runtime["revision"])
            if request.project_revision != current_revision:
                return self._authorization(
                    request, "DENIED", ("STALE_REVISION",), (), (), current_revision
                )
            self.capability_policy.authorize(
                request.role, "filesystem.read", resource="context-expansion", action="read-indexed-source"
            )
            if not request.artifact_snapshot_id or not request.dependency_snapshot_id:
                return self._authorization(
                    request, "UNAVAILABLE", ("INDEX_SNAPSHOT_UNAVAILABLE",), (), (), current_revision
                )
            session = self.store.get_session(request.session_id)
            artifacts = self.derived.read_artifact_snapshot(
                request.session_id, request.artifact_snapshot_id
            )
            graph = self.derived.read_dependency_snapshot(
                request.session_id, request.dependency_snapshot_id
            )
            for snapshot in (artifacts, graph):
                if (
                    snapshot["project_id"] != session.project_id
                    or snapshot["project_revision"] != current_revision
                    or snapshot["policy_hash"] != self.policy_hash
                ):
                    return self._authorization(
                        request, "DENIED", ("STALE_OR_UNBOUND_INDEX",), (), (), current_revision
                    )
            records = {str(item["artifact_id"]): item for item in artifacts["records"]}
            requested = set(request.missing_dependency_refs)
            if not requested.issubset(records):
                return self._authorization(
                    request, "DENIED", ("ARTIFACT_NOT_INDEXED",), (), requested, current_revision
                )
            edges = list(graph["edges"])
            closure = set(requested)
            queue = list(sorted(requested))
            while queue:
                source = queue.pop(0)
                for edge in edges:
                    if edge["source"] != source:
                        continue
                    if edge["confidence"] != "explicit":
                        return self._authorization(
                            request, "DENIED", ("UNKNOWN_DEPENDENCY",), (), closure | {edge["target"]}, current_revision
                        )
                    target = str(edge["target"])
                    if target not in closure:
                        closure.add(target)
                        queue.append(target)
            if not closure.issubset(records):
                return self._authorization(
                    request, "DENIED", ("DEPENDENCY_NOT_INDEXED",), (), closure, current_revision
                )
            resolved: list[str] = []
            for artifact_id in sorted(closure):
                record = records[artifact_id]
                if record["kind"] not in request.desired_source_kinds:
                    return self._authorization(
                        request, "DENIED", ("SOURCE_KIND_NOT_AUTHORIZED",), (), closure, current_revision
                    )
                if record["freshness"] != "CURRENT" or record["project_revision"] != current_revision:
                    return self._authorization(
                        request, "DENIED", ("STALE_ARTIFACT",), (), closure, current_revision
                    )
                if record["policy_hash"] != self.policy_hash:
                    return self._authorization(
                        request, "DENIED", ("POLICY_HASH_MISMATCH",), (), closure, current_revision
                    )
                authority = str(record["authority"])
                if authority not in AUTHORITY_VALUES:
                    return self._authorization(
                        request, "DENIED", ("AUTHORITY_UNVERIFIED",), (), closure, current_revision
                    )
                if request.requested_level == "L3" and authority == "GENERATED_ARTIFACT":
                    return self._authorization(
                        request, "DENIED", ("L3_AUTHORITY_REQUIRES_VERIFIED_SOURCE",), (), closure, current_revision
                    )
                try:
                    self.path_policy.assert_path(
                        request.role, root, str(record["locator"]), operation="read"
                    )
                except PathAccessDenied:
                    return self._authorization(
                        request, "DENIED", ("ROLE_PATH_DENIED",), (), closure, current_revision
                    )
                resolved.append(artifact_id)
            return self._authorization(
                request, "APPROVED", (), resolved, closure, current_revision
            )
        except RuntimeStorageError:
            return self._authorization(
                request, "UNAVAILABLE", ("INDEX_OR_GRAPH_UNAVAILABLE",), (), (), request.project_revision
            )
        except RuntimeValidationError as exc:
            return self._authorization(
                request, "DENIED", (str(exc),), (), (), request.project_revision
            )

    def deliver(
        self,
        request: ContextRequest,
        authorization: ContextAuthorization,
        current_context: Any,
        *,
        total_delivered_bytes: int = 0,
    ) -> ContextExpansion:
        """读取授权后的 locator，并将扩展结果与正式 Context 分离。"""

        expansion_id = stable_id(
            "f14-context-expansion", request.request_id, authorization.authorization_hash
        )
        if authorization.request_id != request.request_id:
            return self._denied_expansion(request, expansion_id, authorization, "AUTHORIZATION_BINDING_MISMATCH")
        if authorization.decision == "UNAVAILABLE":
            return self._fallback_expansion(request, expansion_id, authorization, "INDEX_OR_GRAPH_UNAVAILABLE")
        if authorization.decision != "APPROVED":
            return self._denied_expansion(
                request,
                expansion_id,
                authorization,
                authorization.reasons[0] if authorization.reasons else "AUTHORIZATION_DENIED",
            )
        if self._context_hash(current_context) != request.current_context_hash:
            return self._denied_expansion(request, expansion_id, authorization, "CURRENT_CONTEXT_HASH_MISMATCH")
        try:
            root, _, runtime = self._runtime_state(request)
            if int(runtime["revision"]) != request.project_revision:
                return self._denied_expansion(request, expansion_id, authorization, "STALE_REVISION")
            artifacts = self.derived.read_artifact_snapshot(
                request.session_id, request.artifact_snapshot_id
            )
            records = {str(item["artifact_id"]): item for item in artifacts["records"]}
            sources: list[ExpandedSource] = []
            delivered = total_delivered_bytes
            for artifact_id in authorization.resolved_artifact_ids:
                record = records.get(artifact_id)
                if record is None:
                    return self._denied_expansion(request, expansion_id, authorization, "ARTIFACT_NOT_INDEXED")
                path = self.path_policy.assert_path(
                    request.role, root, str(record["locator"]), operation="read"
                )
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != record["content_hash"]:
                    return self._denied_expansion(request, expansion_id, authorization, "SOURCE_HASH_MISMATCH")
                if _SECRET_PATTERN.search(raw.decode("utf-8", errors="replace")):
                    return self._denied_expansion(request, expansion_id, authorization, "CONTEXT_SECRET_FORBIDDEN")
                content = None
                source_bytes = 0
                if request.requested_level in {"L2", "L3"}:
                    content = raw.decode("utf-8")
                    source_bytes = len(raw)
                if delivered + source_bytes > self.max_total_bytes or delivered + source_bytes > request.requested_budget_bytes:
                    return self._denied_expansion(request, expansion_id, authorization, "EXPANSION_BUDGET_EXCEEDED")
                delivered += source_bytes
                sources.append(
                    ExpandedSource(
                        artifact_id=artifact_id,
                        kind=str(record["kind"]),
                        locator=str(record["locator"]),
                        content_hash=str(record["content_hash"]),
                        level=request.requested_level,
                        delivery_mode="REFERENCE" if content is None else "INLINE",
                        content=content,
                    )
                )
            expansion = ContextExpansion(
                expansion_id=expansion_id,
                request_id=request.request_id,
                status="DELIVERED",
                level=request.requested_level,
                reasons=(),
                sources=tuple(sources),
                delivered_bytes=delivered - total_delivered_bytes,
                authorization_id=authorization.authorization_id,
            )
            level_counter = (
                "l1_completed"
                if request.requested_level == "L1"
                else f"{request.requested_level.casefold()}_required"
            )
            self.telemetry.record_context_escalation(
                expansion_rounds=1,
                expansion_sources=len(sources),
                expansion_bytes=expansion.delivered_bytes,
                **({level_counter: 1} if level_counter in {"l1_completed", "l2_required", "l3_required"} else {}),
            )
            self._persist_expansion(request, expansion)
            self.store.append_event(
                request.session_id,
                EventType.CONTEXT_EXPANDED,
                ActorType.ORCHESTRATOR,
                "context-escalation-runtime",
                idempotency_key=f"context-expanded:{expansion_id}",
                correlation_id=request.request_id,
                payload={
                    "request_ref": _audit_ref(request.request_id),
                    "expansion_ref": _audit_ref(expansion_id),
                    "level": request.requested_level,
                    "source_count": len(sources),
                    "delivered_bytes": expansion.delivered_bytes,
                    "formal_context_unchanged": True,
                    "llm_invocations": 0,
                },
            )
            return expansion
        except (OSError, UnicodeDecodeError, PathAccessDenied, RuntimeStorageError):
            return self._fallback_expansion(request, expansion_id, authorization, "EXPANSION_RUNTIME_UNAVAILABLE")

    def recover(self, request: ContextRequest, current_context: Any, reason: str) -> ContextRecovery:
        context_hash = self._context_hash(current_context)
        payload = {
            "request_id": request.request_id,
            "status": "F13_FALLBACK",
            "reason": reason,
            "fallback_context_hash": context_hash,
            "preserved_formal_context": True,
        }
        recovery_id = stable_id("f14-context-recovery", request.request_id, context_hash, reason)
        recovery = ContextRecovery(
            recovery_id=recovery_id,
            request_id=request.request_id,
            status="F13_FALLBACK",
            reason=reason,
            fallback_context_hash=context_hash,
            preserved_formal_context=True,
        )
        self.derived.write_context_recovery(
            session_id=request.session_id,
            request_id=request.request_id,
            recovery_id=recovery_id,
            recovery=payload,
            status=recovery.status,
        )
        self.store.append_event(
            request.session_id,
            EventType.CONTEXT_RECOVERED,
            ActorType.ORCHESTRATOR,
            "context-escalation-runtime",
            idempotency_key=f"context-recovered:{recovery_id}",
            correlation_id=request.request_id,
            payload={
                "request_ref": _audit_ref(request.request_id),
                "recovery_ref": _audit_ref(recovery_id),
                "status": recovery.status,
                "reason": _audit_reason(reason),
                "fallback_context_hash": context_hash,
                "preserved_formal_context": True,
                "llm_invocations": 0,
            },
        )
        return recovery

    def run_expansion_loop(
        self,
        current_context: Any,
        requests: Iterable[ContextRequest],
    ) -> ExpansionLoopResult:
        """按 L1→L2→L3 受控处理请求，失败即恢复到 F13。"""

        expansions: list[ContextExpansion] = []
        seen_request_ids: set[str] = set()
        seen_dependencies: set[str] = set()
        total_bytes = 0
        context_hash = self._context_hash(current_context)
        for index, request in enumerate(requests):
            # 先落请求证据，确保后续 cycle / binding 拒绝也有外键可追溯。
            self.record_request(request)
            if request.request_id in seen_request_ids:
                recovery = self.recover(request, current_context, "EXPANSION_CYCLE")
                return ExpansionLoopResult("FALLBACK_F13", current_context, tuple(expansions), recovery, total_bytes)
            seen_request_ids.add(request.request_id)
            if (
                request.current_context_hash != context_hash
                or request.expansion_count != index
                or request.project_revision < 0
            ):
                recovery = self.recover(request, current_context, "EXPANSION_LOOP_BINDING_MISMATCH")
                return ExpansionLoopResult("FALLBACK_F13", current_context, tuple(expansions), recovery, total_bytes)
            if seen_dependencies.intersection(request.missing_dependency_refs):
                recovery = self.recover(request, current_context, "EXPANSION_CYCLE")
                return ExpansionLoopResult("FALLBACK_F13", current_context, tuple(expansions), recovery, total_bytes)
            seen_dependencies.update(request.missing_dependency_refs)
            if index >= self.max_expansions:
                recovery = self.recover(request, current_context, "MAX_EXPANSION_COUNT_EXCEEDED")
                return ExpansionLoopResult("FALLBACK_F13", current_context, tuple(expansions), recovery, total_bytes)
            authorization = self.authorize(request)
            expansion = self.deliver(
                request,
                authorization,
                current_context,
                total_delivered_bytes=total_bytes,
            )
            expansions.append(expansion)
            if expansion.status != "DELIVERED":
                reason = expansion.reasons[0] if expansion.reasons else "EXPANSION_FAILED"
                recovery = self.recover(request, current_context, reason)
                status = "FALLBACK_F13" if authorization.decision == "UNAVAILABLE" else "BLOCKED"
                return ExpansionLoopResult(status, current_context, tuple(expansions), recovery, total_bytes)
            total_bytes += expansion.delivered_bytes
        return ExpansionLoopResult("COMPLETED", current_context, tuple(expansions), None, total_bytes)

    def _persist_expansion(self, request: ContextRequest, expansion: ContextExpansion) -> None:
        self.derived.write_context_expansion(
            session_id=request.session_id,
            request_id=request.request_id,
            expansion_id=expansion.expansion_id,
            expansion=expansion.to_dict(),
            status=expansion.status,
        )

    def _denied_expansion(
        self,
        request: ContextRequest,
        expansion_id: str,
        authorization: ContextAuthorization,
        reason: str,
    ) -> ContextExpansion:
        expansion = ContextExpansion(
            expansion_id=expansion_id,
            request_id=request.request_id,
            status="DENIED",
            level=request.requested_level,
            reasons=(reason,),
            sources=(),
            delivered_bytes=0,
            authorization_id=authorization.authorization_id,
        )
        self._persist_expansion(request, expansion)
        self.telemetry.record_context_escalation(denied=1)
        self.telemetry.record_quality(context_expansion_denied=1)
        self.store.append_event(
            request.session_id,
            EventType.CONTEXT_EXPANSION_DENIED,
            ActorType.ORCHESTRATOR,
            "context-escalation-runtime",
            idempotency_key=f"context-expansion-denied:{expansion_id}",
            correlation_id=request.request_id,
            payload={
                "request_ref": _audit_ref(request.request_id),
                "expansion_ref": _audit_ref(expansion_id),
                "reason": _audit_reason(reason),
                "level": request.requested_level,
            },
        )
        return expansion

    def _fallback_expansion(
        self,
        request: ContextRequest,
        expansion_id: str,
        authorization: ContextAuthorization,
        reason: str,
    ) -> ContextExpansion:
        expansion = ContextExpansion(
            expansion_id=expansion_id,
            request_id=request.request_id,
            status="FALLBACK_F13",
            level=request.requested_level,
            reasons=(reason,),
            sources=(),
            delivered_bytes=0,
            authorization_id=authorization.authorization_id,
        )
        self._persist_expansion(request, expansion)
        self.telemetry.record_context_escalation(unavailable=1, fallback_f13=1)
        self.telemetry.record_quality(fallback_f13_count=1)
        return expansion


__all__ = [
    "ESCALATION_LEVELS",
    "ContextAuthorization",
    "ContextEscalationRuntime",
    "ContextExpansion",
    "ContextRecovery",
    "ContextRequest",
    "ExpansionLoopResult",
    "ExpandedSource",
]
