"""F14-C6 关键覆盖遗漏、故障注入和角色隔离回归。"""

from __future__ import annotations

import pytest

from runtime.context import (
    ContextSemanticModel,
    EnforcedCoverageGate,
    ShadowClassifier,
    ShadowComparisonBuilder,
    ShadowCoverageGate,
)
from runtime.errors import RuntimeValidationError
from tests.test_f14_c_shadow_coverage import _semantic, _unit


def _gate(semantic, current, candidate=None, edges=()):
    classification = ShadowClassifier().classify(
        semantic,
        current,
        dependency_edges=edges,
    )
    comparison = ShadowComparisonBuilder().compare(
        classification, current, candidate if candidate is not None else current
    )
    return classification, comparison, ShadowCoverageGate().evaluate(comparison)


def test_case_missing_acceptance_criterion_is_not_pass() -> None:
    semantic = _semantic()
    current = [_unit("REQ-007", "mandatory")]
    semantic = ContextSemanticModel(
        task_identity=semantic.task_identity,
        role=semantic.role,
        project_revision=semantic.project_revision,
        dependency_roots={"requirements": ("REQ-007",), "acceptance_criteria": ("AC-014",)},
        constraints=semantic.constraints,
        context_classes=semantic.context_classes,
        model_hash=semantic.model_hash,
    )
    _, comparison, gate = _gate(semantic, current)
    assert "AC-014" in comparison.mandatory_missing
    assert gate.result == "FAIL"


def test_case_unknown_edge_connected_to_mandatory_is_unknown_impact() -> None:
    semantic = _semantic()
    current = [_unit("REQ-007", "mandatory")]
    classification, comparison, gate = _gate(
        semantic,
        current,
        edges=[
            {"source": "REQ-007", "target": "PRIVACY-001", "confidence": "unknown"}
        ],
    )
    assert "PRIVACY-001" in classification.mandatory_ids
    assert "PRIVACY-001" in comparison.mandatory_missing
    assert gate.result == "FAIL"


def test_case_requirement_plan_conflict_is_not_pass() -> None:
    semantic = _semantic()
    first = _unit("REQ-007", "mandatory", suffix="first")
    second = _unit("REQ-007", "mandatory", suffix="second")
    classification, comparison, gate = _gate(semantic, [first, second], [first])
    assert "REQ-007" in classification.conflict_ids
    assert "REQ-007" in comparison.mandatory_conflict
    assert gate.result == "FAIL"


def test_case_stale_approved_scope_is_fail_closed() -> None:
    semantic = _semantic()
    stale = _unit("REQ-007", "mandatory", revision=semantic.project_revision - 1)
    _, comparison, gate = _gate(semantic, [stale], [stale])
    assert comparison.mandatory_stale == ("REQ-007",)
    assert gate.result == "FAIL"
    assert EnforcedCoverageGate().enforce(comparison).allow_model_invocation is False


def test_case_optimizer_unavailable_falls_back_without_empty_context() -> None:
    seen = []
    decision, result = EnforcedCoverageGate().invoke_with_fallback(
        {"sources": ["safe-f13"]},
        lambda: (_ for _ in ()).throw(OSError("index unavailable")),
        lambda context: seen.append(context) or "called-with-f13",
    )
    assert decision.result == "FALLBACK_F13"
    assert result == "called-with-f13"
    assert seen == [{"sources": ["safe-f13"]}]


def test_case_role_scope_is_not_cross_role_coverage() -> None:
    generator = _semantic()
    evaluator_units = {
        key: tuple(
            _unit(
                unit.id,
                unit.context_class,
                authority=unit.authority,
                revision=unit.project_revision,
                size=unit.size,
                role_scope="evaluator",
            )
            for unit in values
        )
        for key, values in generator.context_classes.items()
    }
    evaluator = ContextSemanticModel(
        task_identity=generator.task_identity,
        role="evaluator",
        project_revision=generator.project_revision,
        dependency_roots=generator.dependency_roots,
        constraints=generator.constraints,
        context_classes=evaluator_units,
        model_hash=generator.model_hash,
    )
    assert generator.role != evaluator.role
    generator_unit = _unit("REQ-007", "mandatory")
    evaluator_unit = _unit("REQ-007", "mandatory")
    assert generator_unit.role_scope == evaluator_unit.role_scope
    assert evaluator.role == "evaluator"


def test_case_candidate_missing_security_constraint_never_passes() -> None:
    semantic = _semantic(global_constraints=("SECURITY-002",))
    current = [_unit("REQ-007", "mandatory")]
    _, comparison, gate = _gate(semantic, current, candidate=current)
    assert "SECURITY-002" in comparison.mandatory_missing
    assert gate.result == "FAIL"
