"""F14-C5 Selective Context Candidate 的评估器。

本阶段只比较候选，不改变默认 Context，也不提供任意路径读取能力。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from runtime.errors import RuntimeValidationError

from .models import ContextOmittedSource, ContextPackage, ContextSource
from .semantic import ContextUnit
from .shadow import ShadowComparison


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class SelectiveCandidateEvaluation:
    candidate_id: str
    current_context_bytes: int
    candidate_context_bytes: int
    reduction_ratio: float
    mandatory_coverage: str
    unknown_count: int
    authority_verified: bool
    eligible: bool
    fallback_to_f13: bool
    reasons: tuple[str, ...]
    candidate_source_ids: tuple[str, ...]
    evaluation_hash: str

    @property
    def reduction_bytes(self) -> int:
        return max(0, self.current_context_bytes - self.candidate_context_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "current_context_bytes": self.current_context_bytes,
            "candidate_context_bytes": self.candidate_context_bytes,
            "reduction_ratio": self.reduction_ratio,
            "mandatory_coverage": self.mandatory_coverage,
            "unknown_count": self.unknown_count,
            "authority_verified": self.authority_verified,
            "eligible": self.eligible,
            "fallback_to_f13": self.fallback_to_f13,
            "reasons": list(self.reasons),
            "candidate_source_ids": list(self.candidate_source_ids),
            "evaluation_hash": self.evaluation_hash,
        }


class SelectiveContextEvaluator:
    """仅生成可审计候选评估，不负责模型调用。"""

    def evaluate(
        self,
        comparison: ShadowComparison,
        candidate_units: Iterable[ContextUnit],
        *,
        candidate_id: str,
    ) -> SelectiveCandidateEvaluation:
        if not candidate_id or not isinstance(candidate_id, str):
            raise RuntimeValidationError("SELECTIVE_CANDIDATE_ID_INVALID")
        units = tuple(sorted(candidate_units, key=lambda unit: unit.id))
        authority_verified = all(unit.authority != "UNKNOWN" for unit in units)
        unknown_count = len(comparison.mandatory_unknown) + sum(
            unit.authority == "UNKNOWN" for unit in units
        )
        reasons: list[str] = []
        if not comparison.mandatory_complete:
            reasons.append("mandatory_coverage_incomplete")
        if unknown_count:
            reasons.append("unknown_dependency_or_authority")
        if not authority_verified:
            reasons.append("authority_unverified")
        if comparison.mandatory_stale:
            reasons.append("stale_source")
        if comparison.mandatory_conflict:
            reasons.append("authority_or_hash_conflict")
        eligible = not reasons
        payload = {
            "candidate_id": candidate_id,
            "current_context_bytes": comparison.current_context_bytes,
            "candidate_context_bytes": comparison.candidate_context_bytes,
            "reduction_ratio": comparison.potential_reduction_ratio,
            "mandatory_coverage": "COMPLETE" if comparison.mandatory_complete else "INCOMPLETE",
            "unknown_count": unknown_count,
            "authority_verified": authority_verified,
            "eligible": eligible,
            "fallback_to_f13": not eligible,
            "reasons": reasons,
            "candidate_source_ids": [unit.id for unit in units],
        }
        return SelectiveCandidateEvaluation(
            candidate_id=candidate_id,
            current_context_bytes=comparison.current_context_bytes,
            candidate_context_bytes=comparison.candidate_context_bytes,
            reduction_ratio=comparison.potential_reduction_ratio,
            mandatory_coverage=payload["mandatory_coverage"],
            unknown_count=unknown_count,
            authority_verified=authority_verified,
            eligible=eligible,
            fallback_to_f13=not eligible,
            reasons=tuple(reasons),
            candidate_source_ids=tuple(unit.id for unit in units),
            evaluation_hash=hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
        )


class SelectiveContextBuilder:
    """从已通过 F13 权威校验的 Package 派生 Selective Package。"""

    def build(
        self,
        current: ContextPackage,
        *,
        selected_references: Iterable[str],
        mandatory_references: Iterable[str] = (),
        persist_store: Any | None = None,
    ) -> ContextPackage:
        selected = tuple(sorted(set(selected_references)))
        mandatory = tuple(sorted(set(mandatory_references)))
        source_map = {source.reference: source for source in current.sources}
        missing = [reference for reference in mandatory if reference not in source_map]
        if missing:
            raise RuntimeValidationError(
                "F14_SELECTIVE_MANDATORY_SOURCE_MISSING:" + ",".join(missing)
            )
        unknown = [reference for reference in selected if reference not in source_map]
        if unknown:
            raise RuntimeValidationError(
                "F14_SELECTIVE_SOURCE_UNKNOWN:" + ",".join(unknown)
            )
        selected_set = set(selected) | set(mandatory)
        sources = tuple(source_map[reference] for reference in sorted(selected_set))
        omitted = list(current.omitted_sources)
        omitted.extend(
            ContextOmittedSource(
                reference=source.reference,
                content_hash=source.content_hash,
                reason=source.reason,
                priority=source.priority,
                omission_reason="F14_SELECTIVE_NOT_TASK_RELEVANT",
                size=source.size,
            )
            for source in current.sources
            if source.reference not in selected_set
        )
        omitted = sorted({item.reference: item for item in omitted}.values(), key=lambda item: item.reference)
        inline_bytes = sum(source.included_size for source in sources)
        inline_count = sum(source.delivery_mode == "INLINE" for source in sources)
        reference_count = sum(source.delivery_mode == "REFERENCE" for source in sources)
        excluded_sources = tuple(sorted(set(current.excluded_sources) | {"F14_SELECTIVE_DERIVED"}))
        manifest_without_hash = {
            "session_id": current.session_id,
            "run_id": current.run_id,
            "role": current.role,
            "project_id": current.project_id,
            "workflow_state": current.workflow_state,
            "project_revision": current.project_revision,
            "created_at": current.created_at,
            "project_state_hash": current.project_state_hash,
            "context_policy_hash": current.context_policy_hash,
            "budget_fingerprint": current.budget_fingerprint,
            "context_type": current.context_type,
            "excluded_sources": list(excluded_sources),
            "budget_limit": current.budget_limit,
            "budget_used": inline_bytes,
            "inline_bytes": inline_bytes,
            "source_count": len(sources),
            "inline_source_count": inline_count,
            "reference_source_count": reference_count,
            "omitted_source_count": len(omitted),
            "sources": [source.to_dict() for source in sources],
            "omitted_sources": [source.to_dict() for source in omitted],
        }
        context_hash = hashlib.sha256(_canonical(manifest_without_hash).encode("utf-8")).hexdigest()
        package = replace(
            current,
            context_id=f"context-{context_hash[:24]}",
            sources=sources,
            omitted_sources=tuple(omitted),
            budget_used=inline_bytes,
            inline_bytes=inline_bytes,
            source_count=len(sources),
            inline_source_count=inline_count,
            reference_source_count=reference_count,
            omitted_source_count=len(omitted),
            context_hash=context_hash,
            excluded_sources=excluded_sources,
        )
        if persist_store is not None:
            persist_store.save_context_manifest(
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
                sources=[source.to_dict() for source in package.sources],
                omitted_sources=[source.to_dict() for source in package.omitted_sources],
                created_at=package.created_at,
            )
        return package


__all__ = [
    "SelectiveCandidateEvaluation",
    "SelectiveContextBuilder",
    "SelectiveContextEvaluator",
]
