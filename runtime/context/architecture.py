"""F14-C Shadow-First Context Architecture 的确定性编排入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from runtime.deterministic.store import DerivedRuntimeStore
from runtime.session_store import SessionStore

from .models import ContextBuildRequest, ContextPackage
from .selective import SelectiveCandidateEvaluation, SelectiveContextEvaluator
from .semantic import ContextSemanticBuilder, ContextSemanticModel, ContextUnit
from .shadow import (
    ShadowClassification,
    ShadowClassifier,
    ShadowComparison,
    ShadowComparisonBuilder,
    ShadowCoverageGate,
    ShadowGateResult,
)


@dataclass(frozen=True)
class ShadowFirstEvaluation:
    semantic: ContextSemanticModel
    classification: ShadowClassification
    comparison: ShadowComparison
    gate: ShadowGateResult
    candidate: SelectiveCandidateEvaluation | None
    semantic_id: str
    comparison_id: str
    gate_id: str
    candidate_id: str | None


class ShadowFirstContextRuntime:
    """生成并持久化 C1–C5 旁路证据，不替换 F13 Context。"""

    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.derived = DerivedRuntimeStore(store)

    def evaluate(
        self,
        request: ContextBuildRequest,
        current: ContextPackage,
        *,
        current_units: Iterable[ContextUnit] | None = None,
        candidate_units: Iterable[ContextUnit] | None = None,
        global_mandatory_units: Iterable[ContextUnit] = (),
        dependency_edges: Iterable[Any] = (),
        candidate_id: str | None = None,
        dependency_roots: Mapping[str, tuple[str, ...]] | None = None,
        constraints: Mapping[str, tuple[str, ...]] | None = None,
    ) -> ShadowFirstEvaluation:
        semantic = ContextSemanticBuilder().build(
            request,
            current,
            dependency_roots=dependency_roots,
            constraints=constraints,
        )
        source_units = list(current_units or [
            unit
            for values in semantic.context_classes.values()
            for unit in values
        ])
        proposed_units = list(candidate_units if candidate_units is not None else source_units)
        classification = ShadowClassifier().classify(
            semantic,
            source_units,
            global_mandatory_units=global_mandatory_units,
            dependency_edges=dependency_edges,
        )
        comparison = ShadowComparisonBuilder().compare(
            classification, source_units, proposed_units
        )
        gate = ShadowCoverageGate().evaluate(comparison)
        candidate = None
        if candidate_id is not None:
            candidate = SelectiveContextEvaluator().evaluate(
                comparison,
                proposed_units,
                candidate_id=candidate_id,
            )
        session = self.store.get_session(request.session_id)
        semantic_id = self.derived.write_context_semantic_snapshot(
            session_id=request.session_id,
            run_id=request.run_id,
            project_id=session.project_id,
            role=request.role,
            project_revision=current.project_revision,
            semantic=semantic,
        )
        comparison_id = self.derived.write_shadow_comparison(
            session_id=request.session_id,
            run_id=request.run_id,
            project_id=session.project_id,
            role=request.role,
            project_revision=current.project_revision,
            comparison=comparison,
        )
        gate_id = self.derived.write_shadow_gate_result(
            session_id=request.session_id,
            run_id=request.run_id,
            comparison_id=comparison_id,
            gate=gate.to_dict(),
        )
        candidate_evaluation_id = None
        if candidate is not None:
            candidate_evaluation_id = self.derived.write_selective_candidate(
                session_id=request.session_id,
                run_id=request.run_id,
                evaluation=candidate,
            )
        return ShadowFirstEvaluation(
            semantic=semantic,
            classification=classification,
            comparison=comparison,
            gate=gate,
            candidate=candidate,
            semantic_id=semantic_id,
            comparison_id=comparison_id,
            gate_id=gate_id,
            candidate_id=candidate_evaluation_id,
        )


__all__ = ["ShadowFirstContextRuntime", "ShadowFirstEvaluation"]
