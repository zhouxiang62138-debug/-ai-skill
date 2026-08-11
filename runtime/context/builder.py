"""F13.1/F13.2 正式 Deterministic Context Builder。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from runtime.event_types import ActorType, EventType
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.policy import CapabilityPolicy, load_runtime_routes
from runtime.project_revision import project_state_hash, runtime_projection
from runtime.session_store import SessionStore
from scripts.project_state import ProjectStateError, load_project_state

from .models import (
    ContextBuildRequest,
    ContextOmittedSource,
    ContextPackage,
    ContextResumePackage,
    ContextSource,
    ContextSourceDelta,
)
from .policy import ContextBudgetConfig, ContextPolicy, ContextSourceRule
from runtime.execution.path_policy import ExecutionPathPolicy


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|private[_-]?key|authorization|token)"
    r"\s*[:=]\s*[^\s,;]+"
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|(?<![A-Za-z0-9_])sk-[A-Za-z0-9]+)"
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_reference(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeValidationError("CONTEXT_REFERENCE_INVALID")
    normalized = value.replace("\\", "/")
    if "\n" in normalized or "\r" in normalized:
        raise RuntimeValidationError("CONTEXT_REFERENCE_INVALID")
    pure = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if pure.is_absolute() or windows.is_absolute() or windows.drive or ".." in pure.parts:
        raise RuntimeValidationError("CONTEXT_REFERENCE_INVALID")
    return "." if normalized == "." else "/".join(pure.parts)


def _assert_secret_free(content: str) -> None:
    """拒绝明显 Secret 载荷；不会把扫描器当成 F12 Credential Boundary。"""

    if _SECRET_ASSIGNMENT.search(content) or _SECRET_VALUE.search(content):
        raise RuntimeValidationError("CONTEXT_SECRET_FORBIDDEN")


class ContextBuilder:
    """只读取当前 Session 对应项目，并生成可复现 Context Package。"""

    def __init__(
        self,
        store: SessionStore,
        *,
        context_policy: ContextPolicy | None = None,
        path_policy: ExecutionPathPolicy | None = None,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        self._store = store
        self._context_policy = context_policy or ContextPolicy()
        self._path_policy = path_policy or ExecutionPathPolicy()
        self._capability_policy = capability_policy or CapabilityPolicy()

    def build(self, request: ContextBuildRequest) -> ContextPackage:
        """根据 F10 Session、当前 project.yaml 与配置生成 Context。"""

        if request.role not in self._context_policy.roles:
            raise RuntimeValidationError("CONTEXT_UNKNOWN_ROLE")
        try:
            self._capability_policy.authorize(request.role, "filesystem.read")
        except RuntimeValidationError as exc:
            raise RuntimeValidationError("CONTEXT_CAPABILITY_DENIED") from exc

        session = self._store.get_session(request.session_id)
        root = Path(session.project_root).resolve()
        project_yaml = root / "project.yaml"
        if not project_yaml.is_file():
            raise RuntimeValidationError("CONTEXT_PROJECT_STATE_MISSING")
        if not root.is_dir():
            raise RuntimeValidationError("CONTEXT_PROJECT_BOUNDARY")

        self._assert_read_path(request.role, root, "project.yaml")
        state = load_project_state(project_yaml)
        try:
            runtime = runtime_projection(state)
        except Exception as exc:
            raise RuntimeValidationError("CONTEXT_RUNTIME_STATE_INVALID") from exc
        if runtime.get("session_id") != request.session_id:
            raise RuntimeValidationError("CONTEXT_SESSION_PROJECT_MISMATCH")
        if state.get("project_id") != session.project_id:
            raise RuntimeValidationError("CONTEXT_PROJECT_BOUNDARY")
        workflow_state = state.get("status")
        if not isinstance(workflow_state, str):
            raise RuntimeValidationError("CONTEXT_WORKFLOW_STATE_INVALID")
        self._validate_workflow_role(workflow_state, request.role, state)
        self._validate_role_run(request, session)
        revision = runtime.get("revision")
        if not isinstance(revision, int) or revision < 0:
            raise RuntimeValidationError("CONTEXT_REVISION_INVALID")

        candidates = self._collect_sources(
            request.role,
            root,
            state,
            request.additional_references,
        )
        sources, omitted_sources, budget_used = self._select_sources(
            request.role, candidates
        )
        budget = self._context_policy.budget_for(request.role)
        context_type = (
            "EVALUATOR_INDEPENDENT" if request.role == "evaluator" else "ROLE_SCOPED"
        )
        excluded_sources = (
            tuple(sorted(self._context_policy.evaluator_excluded_source_labels))
            if request.role == "evaluator"
            else ()
        )
        budget_fingerprint = hashlib.sha256(
            _canonical(budget.to_dict()).encode("utf-8")
        ).hexdigest()
        inline_source_count = sum(
            source.delivery_mode == "INLINE" for source in sources
        )
        reference_source_count = sum(
            source.delivery_mode == "REFERENCE" for source in sources
        )
        state_hash = project_state_hash(state)
        manifest_without_hash = {
            "session_id": request.session_id,
            "run_id": request.run_id,
            "role": request.role,
            "project_id": session.project_id,
            "workflow_state": workflow_state,
            "project_revision": revision,
            "created_at": session.created_at,
            "project_state_hash": state_hash,
            "context_policy_hash": self._context_policy.policy_hash,
            "budget_fingerprint": budget_fingerprint,
            "budget_config": budget.to_dict(),
            "context_type": context_type,
            "excluded_sources": list(excluded_sources),
            "budget_limit": budget.max_context_bytes,
            "budget_used": budget_used,
            "inline_bytes": budget_used,
            "source_count": len(sources),
            "inline_source_count": inline_source_count,
            "reference_source_count": reference_source_count,
            "omitted_source_count": len(omitted_sources),
            "sources": [source.to_dict() for source in sources],
            "omitted_sources": [
                source.to_dict() for source in omitted_sources
            ],
        }
        context_hash = hashlib.sha256(
            _canonical(manifest_without_hash).encode("utf-8")
        ).hexdigest()
        context_id = f"context-{context_hash[:24]}"
        package = ContextPackage(
            context_id=context_id,
            session_id=request.session_id,
            run_id=request.run_id,
            role=request.role,
            project_id=session.project_id,
            workflow_state=workflow_state,
            project_revision=revision,
            created_at=session.created_at,
            project_state_hash=state_hash,
            context_policy_hash=self._context_policy.policy_hash,
            budget_fingerprint=budget_fingerprint,
            sources=tuple(sources),
            omitted_sources=tuple(omitted_sources),
            budget_limit=budget.max_context_bytes,
            budget_used=budget_used,
            inline_bytes=budget_used,
            source_count=len(sources),
            inline_source_count=inline_source_count,
            reference_source_count=reference_source_count,
            omitted_source_count=len(omitted_sources),
            max_inline_bytes=budget.max_inline_bytes,
            max_source_inline_bytes=budget.max_source_inline_bytes,
            max_sources=budget.max_sources,
            context_hash=context_hash,
            context_type=context_type,
            excluded_sources=excluded_sources,
        )
        self._store.save_context_manifest(
            context_id=package.context_id,
            session_id=package.session_id,
            project_id=package.project_id,
            run_id=package.run_id,
            role=package.role,
            context_type=package.context_type,
            excluded_sources=list(package.excluded_sources),
            workflow_state=package.workflow_state,
            project_revision=package.project_revision,
            project_state_hash=package.project_state_hash,
            context_hash=package.context_hash,
            context_policy_hash=package.context_policy_hash,
            budget_fingerprint=package.budget_fingerprint,
            sources=[self._durable_source(source) for source in package.sources],
            omitted_sources=[
                source.to_dict() for source in package.omitted_sources
            ],
            created_at=package.created_at,
        )
        actor_type = ActorType.MODULE if request.role in {"first_ask_intake", "reference_analysis"} else ActorType.ROLE
        self._store.append_event(
            request.session_id,
            EventType.CONTEXT_BUILT,
            actor_type,
            request.role,
            idempotency_key=f"context-built:{context_id}",
            correlation_id=request.run_id,
            payload={
                "context_id": context_id,
                "role": request.role,
                "workflow_state": workflow_state,
                "project_revision": revision,
                "context_hash": context_hash,
                "source_count": len(sources),
                "budget_used": budget_used,
                "budget_limit": budget.max_context_bytes,
                "inline_source_count": inline_source_count,
                "reference_source_count": reference_source_count,
                "omitted_source_count": len(omitted_sources),
            },
        )
        return package

    @staticmethod
    def _durable_source(source: ContextSource) -> dict[str, Any]:
        """只保留 Manifest 元数据，禁止把 inline 正文写入 F10。"""

        value = source.to_dict()
        value.pop("content", None)
        return value

    def build_resume(self, request: ContextBuildRequest) -> ContextResumePackage:
        """从同一 Session 的 durable Manifest 构建确定性 Resume/Delta。"""

        session = self._store.get_session(request.session_id)
        try:
            previous = self._store.find_previous_context_manifest(
                request.session_id,
                project_id=session.project_id,
                role=request.role,
            )
        except RuntimeStorageError as exc:
            if str(exc) == "CONTEXT_MANIFEST_INVALID":
                previous = None
            else:
                raise
        current = self.build(request)
        if previous is None:
            result = self._full_resume(current, None)
        else:
            result = self._resume_from_previous(current, previous)
        actor_type = ActorType.MODULE if request.role in {"first_ask_intake", "reference_analysis"} else ActorType.ROLE
        self._store.append_event(
            request.session_id,
            EventType.CONTEXT_RESUMED,
            actor_type,
            request.role,
            idempotency_key=(
                f"context-resumed:{current.context_id}:"
                f"{result.previous_context_id or 'none'}:{result.resume_mode}"
            ),
            correlation_id=request.run_id,
            payload={
                "context_id": current.context_id,
                "previous_context_id": result.previous_context_id,
                "resume_mode": result.resume_mode,
                "base_revision": result.base_revision,
                "current_revision": result.current_revision,
                "unchanged_count": len(result.unchanged_sources),
                "added_count": len(result.added_sources),
                "modified_count": len(result.modified_sources),
                "removed_count": len(result.removed_sources),
                "final_context_hash": current.context_hash,
            },
        )
        return result

    @staticmethod
    def _full_resume(
        current: ContextPackage, previous: dict[str, Any] | None
    ) -> ContextResumePackage:
        current_map = ContextBuilder._source_map_from_package(current)
        added = tuple(
            ContextSourceDelta(reference=reference, previous_content_hash=None, current_content_hash=content_hash)
            for reference, content_hash in sorted(current_map.items())
        )
        return ContextResumePackage(
            context=current,
            resume_mode="FULL_BUILD",
            previous_context_id=None,
            previous_context_hash=None,
            base_revision=None,
            current_revision=current.project_revision,
            unchanged_sources=(),
            added_sources=added,
            modified_sources=(),
            removed_sources=(),
        )

    def _resume_from_previous(
        self, current: ContextPackage, previous: dict[str, Any]
    ) -> ContextResumePackage:
        try:
            previous_id = str(previous["context_id"])
            previous_hash = str(previous["context_hash"])
            previous_role = str(previous["role"])
            previous_project = str(previous["project_id"])
            previous_state = str(previous["workflow_state"])
            previous_revision = int(previous["project_revision"])
            previous_policy_hash = str(previous["context_policy_hash"])
            previous_budget_fingerprint = str(previous["budget_fingerprint"])
            previous_complete = int(previous["complete"])
            previous_sources = previous["sources"]
            previous_omitted = previous["omitted_sources"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeValidationError("CONTEXT_RESUME_PREVIOUS_INVALID") from exc
        if (
            previous_complete != 1
            or previous_role != current.role
            or previous_project != current.project_id
            or previous_state != current.workflow_state
            or not isinstance(previous_sources, list)
            or not isinstance(previous_omitted, list)
        ):
            return self._full_resume(current, None)
        if previous_revision > current.project_revision:
            raise RuntimeValidationError("CONTEXT_RESUME_REVISION_MISMATCH")
        if (
            previous_policy_hash != current.context_policy_hash
            or previous_budget_fingerprint != current.budget_fingerprint
        ):
            return self._full_resume(current, None)
        previous_map = self._source_map_from_manifest(previous_sources, previous_omitted)
        current_map = self._source_map_from_package(current)
        unchanged, added, modified, removed = self._compare_source_maps(
            previous_map, current_map
        )
        return ContextResumePackage(
            context=current,
            resume_mode="INCREMENTAL",
            previous_context_id=previous_id,
            previous_context_hash=previous_hash,
            base_revision=previous_revision,
            current_revision=current.project_revision,
            unchanged_sources=unchanged,
            added_sources=added,
            modified_sources=modified,
            removed_sources=removed,
        )

    @staticmethod
    def _source_map_from_package(package: ContextPackage) -> dict[str, str]:
        values = {
            source.reference: source.content_hash for source in package.sources
        }
        for source in package.omitted_sources:
            if source.reference in values and values[source.reference] != source.content_hash:
                raise RuntimeValidationError("CONTEXT_RESUME_PREVIOUS_INVALID")
            values[source.reference] = source.content_hash
        return values

    @staticmethod
    def _source_map_from_manifest(
        sources: list[dict[str, Any]], omitted_sources: list[dict[str, Any]]
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for source in [*sources, *omitted_sources]:
            if not isinstance(source, dict):
                raise RuntimeValidationError("CONTEXT_RESUME_PREVIOUS_INVALID")
            reference = source.get("reference")
            content_hash = source.get("content_hash")
            if not isinstance(reference, str) or not isinstance(content_hash, str):
                raise RuntimeValidationError("CONTEXT_RESUME_PREVIOUS_INVALID")
            if reference in values and values[reference] != content_hash:
                raise RuntimeValidationError("CONTEXT_RESUME_PREVIOUS_INVALID")
            values[reference] = content_hash
        return values

    @staticmethod
    def _compare_source_maps(
        previous: dict[str, str], current: dict[str, str]
    ) -> tuple[
        tuple[ContextSourceDelta, ...],
        tuple[ContextSourceDelta, ...],
        tuple[ContextSourceDelta, ...],
        tuple[ContextSourceDelta, ...],
    ]:
        unchanged: list[ContextSourceDelta] = []
        added: list[ContextSourceDelta] = []
        modified: list[ContextSourceDelta] = []
        removed: list[ContextSourceDelta] = []
        for reference in sorted(set(previous) | set(current)):
            previous_hash = previous.get(reference)
            current_hash = current.get(reference)
            delta = ContextSourceDelta(reference, previous_hash, current_hash)
            if previous_hash is None:
                added.append(delta)
            elif current_hash is None:
                removed.append(delta)
            elif previous_hash == current_hash:
                unchanged.append(delta)
            else:
                modified.append(delta)
        return tuple(unchanged), tuple(added), tuple(modified), tuple(removed)

    def _validate_role_run(self, request: ContextBuildRequest, session: Any) -> None:
        if request.role in {"first_ask_intake", "domain_research", "reference_analysis"}:
            if session.status not in {"ACTIVE", "PAUSED"}:
                raise RuntimeValidationError("CONTEXT_MODULE_RUN_INVALID")
            return
        try:
            run = self._store.get_role_run(request.session_id, request.run_id)
        except RuntimeStorageError as exc:
            raise RuntimeValidationError("CONTEXT_ROLE_RUN_MISSING") from exc
        if run.get("status") != "STARTED" or run.get("role") != request.role:
            raise RuntimeValidationError("CONTEXT_ROLE_RUN_INVALID")
        if session.project_root != str(Path(session.project_root).resolve()):
            raise RuntimeValidationError("CONTEXT_PROJECT_BOUNDARY")

    @staticmethod
    def _validate_workflow_role(
        workflow_state: str, role: str, state: dict[str, Any]
    ) -> None:
        if role in {"first_ask_intake", "domain_research", "reference_analysis"}:
            allowed_states = (
                {"INTAKE", "WAITING_FOR_REQUIREMENTS"}
                if role == "first_ask_intake"
                else {"REQUIREMENT_RESEARCH"}
                if role == "domain_research"
                else {"REFERENCE_ANALYSIS"}
            )
            if workflow_state not in allowed_states or state.get("active_module") != role or state.get("next_role") is not None:
                raise RuntimeValidationError("CONTEXT_ROLE_STATE_MISMATCH")
            return
        route = load_runtime_routes().get(workflow_state)
        if route is None or route["wait_for_user"] or route["next_role"] != role:
            raise RuntimeValidationError("CONTEXT_ROLE_STATE_MISMATCH")
        if state.get("next_role") != role:
            raise RuntimeValidationError("CONTEXT_WORKFLOW_STATE_INVALID")

    def _collect_sources(
        self,
        role: str,
        root: Path,
        state: dict[str, Any],
        additional_references: Iterable[str],
    ) -> list[ContextSource]:
        sources: list[ContextSource] = []
        seen: set[tuple[str, str]] = set()
        for rule in self._context_policy.rules_for(role):
            if (
                role == "evaluator"
                and rule.source_type != "state_reference"
                and self._context_policy.evaluator_source_excluded(rule.field, rule.reference)
            ):
                continue
            if rule.source_type == "project_state":
                reference = _normalize_reference(rule.reference)
                source = self._read_file_source(role, root, reference, rule)
            elif rule.source_type == "reference_catalog":
                source = self._read_reference_catalog(role, root, state, rule)
            elif rule.source_type == "design_reference_subset":
                if state.get(rule.field or "") in (None, ""):
                    continue
                source = self._read_design_reference_subset(role, root, state, rule)
            elif rule.source_type == "approved_reference_bindings":
                if state.get(rule.field or "") in (None, ""):
                    continue
                source = self._read_approved_reference_bindings(role, root, rule)
                if source is None:
                    continue
            elif rule.source_type == "reference_conformance_subset":
                if state.get(rule.field or "") in (None, ""):
                    continue
                source = self._read_approved_reference_bindings(role, root, rule)
                if source is None:
                    continue
            elif rule.source_type == "state_reference":
                value = state.get(rule.field or "")
                if value in (None, ""):
                    continue
                if not isinstance(value, str):
                    raise RuntimeValidationError("CONTEXT_STATE_REFERENCE_INVALID")
                reference = _normalize_reference(value)
                if role == "evaluator" and self._context_policy.evaluator_source_excluded(
                    rule.field, reference
                ):
                    continue
                source = self._read_file_source(role, root, reference, rule)
            else:
                value = state.get(rule.field or "")
                if value in (None, ""):
                    continue
                if isinstance(value, (dict, list)):
                    content = _canonical(value)
                elif isinstance(value, (str, int, float, bool)):
                    content = str(value)
                else:
                    raise RuntimeValidationError("CONTEXT_STATE_VALUE_INVALID")
                _assert_secret_free(content)
                reference = f"project.yaml#{rule.field}"
                raw_size = len(content.encode("utf-8"))
                configured_mode = (
                    "REFERENCE"
                    if rule.delivery_mode == "REFERENCE"
                    or rule.priority == "REFERENCE_ONLY"
                    else "INLINE"
                )
                source = ContextSource(
                    source_type=rule.source_type,
                    reference=reference,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    reason=rule.reason,
                    priority=rule.priority,
                    delivery_mode=configured_mode,
                    size=raw_size,
                    original_size=raw_size,
                    included_size=raw_size if configured_mode == "INLINE" else 0,
                    inline_content=content if configured_mode == "INLINE" else None,
                )
            key = (source.source_type, source.reference)
            if key not in seen:
                sources.append(source)
                seen.add(key)

        normalized_additional = sorted(
            {_normalize_reference(reference) for reference in additional_references}
        )
        if role in {"generator", "evaluator"} and state.get("active_reference_synthesis") not in (None, ""):
            for reference in normalized_additional:
                normalized = reference.casefold()
                raw_reference = (
                    normalized.startswith("memory/references/")
                    or normalized.startswith("artifacts/references/")
                    or "/references/" in normalized
                )
                if raw_reference:
                    raise RuntimeValidationError("CONTEXT_GENERATOR_REFERENCE_SCOPE")
                if role == "generator" and "/evidence/" in normalized:
                    raise RuntimeValidationError("CONTEXT_GENERATOR_REFERENCE_SCOPE")
        for reference in normalized_additional:
            if role == "evaluator" and self._context_policy.evaluator_source_excluded(
                None, reference
            ):
                raise RuntimeValidationError("CONTEXT_EVALUATOR_SOURCE_EXCLUDED")
            source = self._read_file_source(
                role,
                root,
                reference,
                ContextSourceRule(
                    source_type="requested_reference",
                    reference=reference,
                    field=None,
                    reason="当前 Role 明确请求且通过 Path Policy 的必要文件引用",
                    priority="HIGH",
                    delivery_mode="INLINE",
                ),
            )
            key = (source.source_type, source.reference)
            if key not in seen:
                sources.append(source)
                seen.add(key)
        return sources

    def _read_reference_catalog(
        self,
        role: str,
        root: Path,
        state: dict[str, Any],
        rule: ContextSourceRule,
    ) -> ContextSource:
        from runtime.reference_analysis.artifacts import ReferenceArtifactStore

        store = ReferenceArtifactStore(root, path_policy=self._path_policy, path_actor=role)
        context = {"type": "new_project", "project_id": str(state.get("project_id")), "change_request_id": None}
        records = []
        for source in store.list_sources(context):
            locator = source.get("source") if isinstance(source.get("source"), dict) else {}
            records.append({
                "reference_id": source.get("reference_id"),
                "source_type": source.get("source_type"),
                "reference_mode": source.get("reference_mode"),
                "requested_scope": source.get("requested_scope"),
                "explicit_inclusions": source.get("explicit_inclusions"),
                "explicit_exclusions": source.get("explicit_exclusions"),
                "status": source.get("status"),
                "source_ref": source.get("_artifact_ref"),
                "scope_ref": source.get("scope_ref"),
                "locator": {key: locator.get(key) for key in ("uri", "artifact_ref", "text_ref", "identifier") if locator.get(key)},
            })
        content = _canonical(records)
        _assert_secret_free(content)
        raw = content.encode("utf-8")
        configured_mode = "REFERENCE" if rule.delivery_mode == "REFERENCE" or rule.priority == "REFERENCE_ONLY" else "INLINE"
        return ContextSource(
            source_type=rule.source_type,
            reference="memory/references/reference-catalog",
            content_hash=hashlib.sha256(raw).hexdigest(),
            reason=rule.reason,
            priority=rule.priority,
            delivery_mode=configured_mode,
            size=len(raw),
            original_size=len(raw),
            included_size=len(raw) if configured_mode == "INLINE" else 0,
            inline_content=content if configured_mode == "INLINE" else None,
        )

    def _read_design_reference_subset(
        self,
        role: str,
        root: Path,
        state: dict[str, Any],
        rule: ContextSourceRule,
    ) -> ContextSource:
        """只把当前 synthesis 的设计决策摘要交给 Planner，不扩散原始 Reference。"""

        pointer = state.get(rule.field or "")
        if not isinstance(pointer, str):
            raise RuntimeValidationError("CONTEXT_DESIGN_REFERENCE_POINTER_INVALID")
        path = self._path_policy.assert_path(role, root, pointer, operation="read")
        try:
            synthesis = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeValidationError("CONTEXT_DESIGN_REFERENCE_READ_FAILED") from exc
        if not isinstance(synthesis, dict):
            raise RuntimeValidationError("CONTEXT_DESIGN_REFERENCE_INVALID")
        from scripts.exploration import select_design_reference_decisions
        from runtime.reference_analysis.artifacts import ReferenceArtifactStore

        try:
            store = ReferenceArtifactStore(root, path_policy=self._path_policy, path_actor=role)
            context = {
                "type": "new_project",
                "project_id": str(state.get("project_id")),
                "change_request_id": None,
            }
            source_metadata = [
                item
                for item in store.list_sources(context)
                if item.get("reference_id") in set(synthesis.get("source_references", []) or [])
            ]
            selected = select_design_reference_decisions(
                synthesis, source_metadata=source_metadata
            )
        except Exception as exc:  # 统一把非当前/损坏 synthesis fail closed
            raise RuntimeValidationError("CONTEXT_DESIGN_REFERENCE_INVALID") from exc
        content = _canonical(
            {
                "synthesis_id": selected["synthesis_id"],
                "reference_mode": selected["reference_mode"],
                "decision_ids": selected["decision_ids"],
                "design_relevant_decisions": selected["decisions"],
                "explicit_exclusions": selected["explicit_exclusions"],
            }
        )
        _assert_secret_free(content)
        raw = content.encode("utf-8")
        return ContextSource(
            source_type=rule.source_type,
            reference=f"{pointer}#design-relevant-decisions",
            content_hash=hashlib.sha256(raw).hexdigest(),
            reason=rule.reason,
            priority=rule.priority,
            delivery_mode="INLINE",
            size=len(raw),
            original_size=len(raw),
            included_size=len(raw),
            inline_content=content,
        )

    def _read_approved_reference_bindings(
        self,
        role: str,
        root: Path,
        rule: ContextSourceRule,
    ) -> ContextSource | None:
        """向 Generator 提供批准后的绑定摘要，不读取原始 Reference 内容。"""

        if role not in {"generator", "evaluator"}:
            raise RuntimeValidationError("CONTEXT_APPROVED_REFERENCE_ROLE_INVALID")
        from runtime.reference_contract import (
            build_reference_contract_for_project,
            canonical_reference_contract,
        )

        try:
            contract = build_reference_contract_for_project(
                root, path_policy=self._path_policy
            )
        except (ProjectStateError, RuntimeValidationError) as exc:
            raise RuntimeValidationError(
                "CONTEXT_APPROVED_REFERENCE_CONTRACT_INVALID:" + str(exc)
            ) from exc
        if contract is None:
            return None
        content = canonical_reference_contract(contract)
        _assert_secret_free(content)
        raw = content.encode("utf-8")
        return ContextSource(
            source_type=rule.source_type,
            reference=(
                f"reference-conformance:{contract['contract_id']}"
                if role == "evaluator"
                else f"approved-reference-bindings:{contract['contract_id']}"
            ),
            content_hash=hashlib.sha256(raw).hexdigest(),
            reason=rule.reason,
            priority=rule.priority,
            delivery_mode="INLINE",
            size=len(raw),
            original_size=len(raw),
            included_size=len(raw),
            inline_content=content,
        )

    def _select_sources(
        self,
        role: str,
        candidates: list[ContextSource],
    ) -> tuple[list[ContextSource], list[ContextOmittedSource], int]:
        """按优先级和 canonical reference 选择完整 Inline 或完整 Reference。"""

        budget = self._context_policy.budget_for(role)
        priority_order = {
            "REQUIRED": 0,
            "HIGH": 1,
            "NORMAL": 2,
            "REFERENCE_ONLY": 3,
        }
        ordered = sorted(
            candidates,
            key=lambda source: (
                priority_order[source.priority],
                source.source_type,
                source.reference,
            ),
        )
        included: list[ContextSource] = []
        omitted: list[ContextOmittedSource] = []
        used = 0
        for candidate in ordered:
            if len(included) >= budget.max_sources:
                if candidate.priority == "REQUIRED":
                    raise RuntimeValidationError("CONTEXT_REQUIRED_BUDGET_EXCEEDED")
                omitted.append(
                    ContextOmittedSource(
                        reference=candidate.reference,
                        content_hash=candidate.content_hash,
                        reason=candidate.reason,
                        priority=candidate.priority,
                        omission_reason="SOURCE_LIMIT",
                        size=candidate.size,
                    )
                )
                continue
            if candidate.delivery_mode == "REFERENCE":
                included.append(candidate)
                continue
            if candidate.size > budget.max_source_inline_bytes:
                if candidate.priority == "REQUIRED":
                    raise RuntimeValidationError("CONTEXT_REQUIRED_BUDGET_EXCEEDED")
                included.append(
                    replace(
                        candidate,
                        delivery_mode="REFERENCE",
                        inline_content=None,
                        included_size=0,
                    )
                )
                continue
            if (
                used + candidate.size > budget.max_context_bytes
                or used + candidate.size > budget.max_inline_bytes
            ):
                if candidate.priority == "REQUIRED":
                    raise RuntimeValidationError("CONTEXT_REQUIRED_BUDGET_EXCEEDED")
                included.append(
                    replace(
                        candidate,
                        delivery_mode="REFERENCE",
                        inline_content=None,
                        included_size=0,
                    )
                )
                continue
            included.append(candidate)
            used += candidate.size
        return included, omitted, used

    def _read_file_source(
        self,
        role: str,
        root: Path,
        reference: str,
        rule: ContextSourceRule,
    ) -> ContextSource:
        candidate = self._assert_read_path(role, root, reference)
        try:
            raw = candidate.read_bytes()
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeValidationError("CONTEXT_SOURCE_ENCODING_INVALID") from exc
        except OSError as exc:
            raise RuntimeValidationError("CONTEXT_SOURCE_READ_FAILED") from exc
        _assert_secret_free(content)
        size = len(raw)
        configured_mode = (
            "REFERENCE"
            if rule.delivery_mode == "REFERENCE" or rule.priority == "REFERENCE_ONLY"
            else "INLINE"
        )
        inline_content = content if configured_mode == "INLINE" else None
        return ContextSource(
            source_type=rule.source_type,
            reference=reference,
            content_hash=hashlib.sha256(raw).hexdigest(),
            reason=rule.reason,
            priority=rule.priority,
            delivery_mode=configured_mode,
            size=size,
            original_size=size,
            included_size=size if configured_mode == "INLINE" else 0,
            inline_content=inline_content,
        )

    def _assert_read_path(self, role: str, root: Path, reference: str) -> Path:
        if role in {"first_ask_intake", "domain_research", "reference_analysis"}:
            return self._path_policy.assert_module_path(role, root, reference, operation="read")
        return self._path_policy.assert_path(role, root, reference, operation="read")


def build_context(
    store: SessionStore,
    session_id: str,
    run_id: str,
    role: str,
    *,
    project_root: str | Path | None = None,
    additional_references: Iterable[str] = (),
    context_policy: ContextPolicy | None = None,
    path_policy: ExecutionPathPolicy | None = None,
    capability_policy: CapabilityPolicy | None = None,
) -> ContextPackage:
    """正式入口；project_root 仅用于校验，项目边界仍由 F10 Session 决定。"""

    if project_root is not None:
        session = store.get_session(session_id)
        if Path(project_root).resolve() != Path(session.project_root).resolve():
            raise RuntimeValidationError("CONTEXT_PROJECT_BOUNDARY")
    return ContextBuilder(
        store,
        context_policy=context_policy,
        path_policy=path_policy,
        capability_policy=capability_policy,
    ).build(
        ContextBuildRequest(
            session_id=session_id,
            run_id=run_id,
            role=role,
            additional_references=tuple(additional_references),
        )
    )


def resume_context(
    store: SessionStore,
    session_id: str,
    run_id: str,
    role: str,
    *,
    project_root: str | Path | None = None,
    additional_references: Iterable[str] = (),
    context_policy: ContextPolicy | None = None,
    path_policy: ExecutionPathPolicy | None = None,
    capability_policy: CapabilityPolicy | None = None,
) -> ContextResumePackage:
    """正式 Resume 入口；项目边界仍由 F10 Session 决定。"""

    if project_root is not None:
        session = store.get_session(session_id)
        if Path(project_root).resolve() != Path(session.project_root).resolve():
            raise RuntimeValidationError("CONTEXT_PROJECT_BOUNDARY")
    return ContextBuilder(
        store,
        context_policy=context_policy,
        path_policy=path_policy,
        capability_policy=capability_policy,
    ).build_resume(
        ContextBuildRequest(
            session_id=session_id,
            run_id=run_id,
            role=role,
            additional_references=tuple(additional_references),
        )
    )
