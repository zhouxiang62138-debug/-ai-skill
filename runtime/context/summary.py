"""F14-E Canonical Summary Cache。

确定性摘要可以作为原始证据的导航/压缩表示，但永远保留精确回源信息。
语义摘要只允许 Shadow、Navigation 或按需提示，不能替代权威 Context。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.deterministic.telemetry import RuntimeTelemetry


SUMMARY_TYPES = frozenset({"DETERMINISTIC", "SEMANTIC"})
AUTHORITY_LEVELS = frozenset({"RUNTIME_EVIDENCE", "SHADOW", "NAVIGATION", "ON_DEMAND_HINT"})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise RuntimeValidationError(f"F14_SUMMARY_{name.upper()}_INVALID")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeValidationError(f"F14_SUMMARY_{name.upper()}_INVALID")
    return value


@dataclass(frozen=True)
class SummarySource:
    """摘要的精确回源绑定。"""

    source_ref: str
    exact_locator: str
    source_hash: str

    def __post_init__(self) -> None:
        _text(self.source_ref, "source_ref")
        _text(self.exact_locator, "exact_locator")
        _sha256(self.source_hash, "source_hash")

    def to_dict(self) -> dict[str, str]:
        return {
            "source_ref": self.source_ref,
            "exact_locator": self.exact_locator,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class CanonicalSummary:
    summary_id: str
    summary_type: str
    source_refs: tuple[SummarySource, ...]
    project_revision: int
    policy_hash: str
    role_scope: str
    generator_type: str
    generator_version: str
    model_id_if_used: str | None
    coverage: Mapping[str, Any]
    omitted_sections: tuple[str, ...]
    authority_level: str
    content: Mapping[str, Any]
    summary_hash: str
    valid: bool = True
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        _text(self.summary_id, "summary_id")
        if self.summary_type not in SUMMARY_TYPES:
            raise RuntimeValidationError("F14_SUMMARY_TYPE_INVALID")
        if not isinstance(self.source_refs, tuple) or not all(
            isinstance(item, SummarySource) for item in self.source_refs
        ):
            raise RuntimeValidationError("F14_SUMMARY_SOURCES_INVALID")
        if not self.source_refs:
            raise RuntimeValidationError("F14_SUMMARY_SOURCES_REQUIRED")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("F14_SUMMARY_REVISION_INVALID")
        _sha256(self.policy_hash, "policy_hash")
        _text(self.role_scope, "role_scope")
        _text(self.generator_type, "generator_type")
        _text(self.generator_version, "generator_version")
        if self.model_id_if_used is not None:
            _text(self.model_id_if_used, "model_id_if_used")
        if not isinstance(self.coverage, Mapping):
            raise RuntimeValidationError("F14_SUMMARY_COVERAGE_INVALID")
        if any(not isinstance(item, str) or not item for item in self.omitted_sections):
            raise RuntimeValidationError("F14_SUMMARY_OMITTED_INVALID")
        if self.authority_level not in AUTHORITY_LEVELS:
            raise RuntimeValidationError("F14_SUMMARY_AUTHORITY_INVALID")
        if not isinstance(self.content, Mapping):
            raise RuntimeValidationError("F14_SUMMARY_CONTENT_INVALID")
        _sha256(self.summary_hash, "summary_hash")
        if _hash(self.payload()) != self.summary_hash:
            raise RuntimeValidationError("F14_SUMMARY_HASH_MISMATCH")
        if not isinstance(self.valid, bool):
            raise RuntimeValidationError("F14_SUMMARY_VALID_INVALID")
        if self.invalid_reason is not None:
            _text(self.invalid_reason, "invalid_reason")
        if self.summary_type == "SEMANTIC" and self.authority_level == "RUNTIME_EVIDENCE":
            raise RuntimeValidationError("F14_SUMMARY_SEMANTIC_AUTHORITY_INVALID")

    def payload(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "summary_type": self.summary_type,
            "source_refs": [item.to_dict() for item in self.source_refs],
            "project_revision": self.project_revision,
            "policy_hash": self.policy_hash,
            "role_scope": self.role_scope,
            "generator_type": self.generator_type,
            "generator_version": self.generator_version,
            "model_id_if_used": self.model_id_if_used,
            "coverage": dict(self.coverage),
            "omitted_sections": list(self.omitted_sections),
            "authority_level": self.authority_level,
            "content": dict(self.content),
        }

    def to_dict(self) -> dict[str, Any]:
        value = self.payload()
        value.update(
            {
                "summary_hash": self.summary_hash,
                "valid": self.valid,
                "invalid_reason": self.invalid_reason,
            }
        )
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalSummary":
        try:
            sources = tuple(SummarySource(**dict(item)) for item in value["source_refs"])
            return cls(
                summary_id=str(value["summary_id"]),
                summary_type=str(value["summary_type"]),
                source_refs=sources,
                project_revision=int(value["project_revision"]),
                policy_hash=str(value["policy_hash"]),
                role_scope=str(value["role_scope"]),
                generator_type=str(value["generator_type"]),
                generator_version=str(value["generator_version"]),
                model_id_if_used=value.get("model_id_if_used"),
                coverage=dict(value["coverage"]),
                omitted_sections=tuple(value.get("omitted_sections", ())),
                authority_level=str(value["authority_level"]),
                content=dict(value["content"]),
                summary_hash=str(value["summary_hash"]),
                valid=bool(value.get("valid", True)),
                invalid_reason=value.get("invalid_reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeValidationError("F14_SUMMARY_RECORD_INVALID") from exc

    def invalidate(self, reason: str) -> "CanonicalSummary":
        _text(reason, "invalid_reason")
        value = self.to_dict()
        value["source_refs"] = self.source_refs
        value["invalid_reason"] = reason
        value["valid"] = False
        return CanonicalSummary(**value)


class DeterministicSummaryBuilder:
    """从结构化证据生成可进入正式 Context 的短摘要。"""

    def build(
        self,
        *,
        summary_id: str,
        source_refs: Iterable[SummarySource],
        project_revision: int,
        policy_hash: str,
        role_scope: str,
        generator_version: str,
        content: Mapping[str, Any],
        coverage: Mapping[str, Any] | None = None,
        omitted_sections: Iterable[str] = (),
    ) -> CanonicalSummary:
        sources = tuple(source_refs)
        payload = {
            "summary_id": summary_id,
            "summary_type": "DETERMINISTIC",
            "source_refs": [item.to_dict() for item in sources],
            "project_revision": project_revision,
            "policy_hash": policy_hash,
            "role_scope": role_scope,
            "generator_type": "python_deterministic",
            "generator_version": generator_version,
            "model_id_if_used": None,
            "coverage": dict(coverage or {}),
            "omitted_sections": sorted(set(omitted_sections)),
            "authority_level": "RUNTIME_EVIDENCE",
            "content": dict(content),
        }
        return CanonicalSummary(
            **{**payload, "source_refs": sources},
            summary_hash=_hash(payload),
        )

    def build_browser_evidence(
        self,
        *,
        summary_id: str,
        source_refs: Iterable[SummarySource],
        project_revision: int,
        policy_hash: str,
        role_scope: str,
        generator_version: str,
        bundle: Mapping[str, Any],
    ) -> CanonicalSummary:
        """从结构化 Browser Run/Step 证据生成可重建摘要，不复制原始长日志。"""

        runs = bundle.get("browser_runs", [])
        steps = bundle.get("browser_evidence", [])
        if not isinstance(runs, list) or not isinstance(steps, list):
            raise RuntimeValidationError("F14_BROWSER_SUMMARY_EVIDENCE_INVALID")
        run_results: dict[str, int] = {}
        for item in runs:
            if isinstance(item, Mapping):
                result = str(item.get("result", "UNKNOWN"))
                run_results[result] = run_results.get(result, 0) + 1
        step_results: dict[str, int] = {}
        for item in steps:
            if isinstance(item, Mapping):
                result = str(item.get("result", "UNKNOWN"))
                step_results[result] = step_results.get(result, 0) + 1
        return self.build(
            summary_id=summary_id,
            source_refs=source_refs,
            project_revision=project_revision,
            policy_hash=policy_hash,
            role_scope=role_scope,
            generator_version=generator_version,
            content={
                "browser_run_count": len(runs),
                "browser_step_count": len(steps),
                "run_results": dict(sorted(run_results.items())),
                "step_results": dict(sorted(step_results.items())),
            },
            coverage={
                "structured_browser_evidence": True,
                "raw_evidence_locator_preserved": True,
            },
        )


class SemanticSummaryShadowBuilder:
    """语义摘要的 Shadow-only 构建器；不提供正式 Context 替换接口。"""

    def build(
        self,
        *,
        summary_id: str,
        source_refs: Iterable[SummarySource],
        project_revision: int,
        policy_hash: str,
        role_scope: str,
        generator_version: str,
        model_id: str,
        content: Mapping[str, Any],
        coverage: Mapping[str, Any] | None = None,
        omitted_sections: Iterable[str] = (),
    ) -> CanonicalSummary:
        sources = tuple(source_refs)
        payload = {
            "summary_id": summary_id,
            "summary_type": "SEMANTIC",
            "source_refs": [item.to_dict() for item in sources],
            "project_revision": project_revision,
            "policy_hash": policy_hash,
            "role_scope": role_scope,
            "generator_type": "llm_semantic_shadow",
            "generator_version": generator_version,
            "model_id_if_used": model_id,
            "coverage": dict(coverage or {}),
            "omitted_sections": sorted(set(omitted_sections)),
            "authority_level": "SHADOW",
            "content": dict(content),
        }
        return CanonicalSummary(
            **{**payload, "source_refs": sources},
            summary_hash=_hash(payload),
        )


class CanonicalSummaryCache:
    """带回源校验的摘要缓存；不提供跨角色或 Evaluator 语义复用。"""

    def __init__(self, store: Any, *, telemetry: RuntimeTelemetry | None = None) -> None:
        self.store = store
        self.telemetry = telemetry or RuntimeTelemetry()

    def put(self, *, session_id: str, project_id: str, summary: CanonicalSummary) -> str:
        from runtime.deterministic.store import DerivedRuntimeStore

        return DerivedRuntimeStore(self.store).write_canonical_summary(
            session_id=session_id,
            project_id=project_id,
            summary=summary,
        )

    def get(
        self,
        *,
        session_id: str,
        summary_id: str,
        current_revision: int,
        policy_hash: str,
        role_scope: str,
        source_hashes: Mapping[str, str],
        available_locators: Iterable[str],
        evaluator: bool = False,
        formal_context: bool = False,
    ) -> CanonicalSummary:
        from runtime.deterministic.store import DerivedRuntimeStore

        try:
            record = DerivedRuntimeStore(self.store).read_canonical_summary(
                session_id, summary_id
            )
            summary = CanonicalSummary.from_mapping(record["summary"])
        except (RuntimeStorageError, RuntimeValidationError):
            self.telemetry.record_summary_invalidation()
            raise RuntimeStorageError("F14_SUMMARY_INVALID")
        validated = validate_summary_sources(
            summary,
            current_revision=current_revision,
            policy_hash=policy_hash,
            role_scope=role_scope,
            source_hashes=source_hashes,
            available_locators=available_locators,
        )
        if not validated.valid:
            self.telemetry.record_summary_invalidation()
            raise RuntimeStorageError("F14_SUMMARY_INVALID")
        if summary.summary_type == "SEMANTIC" and (evaluator or formal_context):
            self.telemetry.record_summary_invalidation()
            raise RuntimeValidationError(
                "F14_EVALUATOR_SEMANTIC_SUMMARY_FORBIDDEN"
                if evaluator
                else "F14_SEMANTIC_SUMMARY_SHADOW_ONLY"
            )
        self.telemetry.record_summary_hit()
        return validated


def validate_summary_sources(
    summary: CanonicalSummary,
    *,
    current_revision: int,
    policy_hash: str,
    role_scope: str,
    source_hashes: Mapping[str, str],
    available_locators: Iterable[str],
) -> CanonicalSummary:
    """校验回源所需 revision、policy、role、locator 和 hash。"""

    if summary.project_revision != current_revision:
        return summary.invalidate("PROJECT_REVISION_CHANGED")
    if summary.policy_hash != policy_hash:
        return summary.invalidate("POLICY_HASH_CHANGED")
    if summary.role_scope != role_scope:
        return summary.invalidate("ROLE_SCOPE_CHANGED")
    locators = set(available_locators)
    for source in summary.source_refs:
        if source.exact_locator not in locators:
            return summary.invalidate("SOURCE_LOCATOR_MISSING")
        if source_hashes.get(source.exact_locator) != source.source_hash:
            return summary.invalidate("SOURCE_HASH_CHANGED")
    return summary


def assert_summary_usable(summary: CanonicalSummary, *, evaluator: bool = False) -> None:
    """在使用摘要前执行 fail-closed 保护。"""

    if not summary.valid:
        raise RuntimeStorageError("F14_SUMMARY_INVALID")
    if summary.summary_type == "SEMANTIC":
        if evaluator:
            raise RuntimeValidationError("F14_EVALUATOR_SEMANTIC_SUMMARY_FORBIDDEN")
        raise RuntimeValidationError("F14_SEMANTIC_SUMMARY_SHADOW_ONLY")


__all__ = [
    "AUTHORITY_LEVELS",
    "CanonicalSummary",
    "CanonicalSummaryCache",
    "DeterministicSummaryBuilder",
    "SemanticSummaryShadowBuilder",
    "SUMMARY_TYPES",
    "SummarySource",
    "assert_summary_usable",
    "validate_summary_sources",
]
