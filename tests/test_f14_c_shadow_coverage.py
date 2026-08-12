"""F14-C2/C3 Shadow 分类、全局约束闭包和 Coverage Gate 测试。"""

from __future__ import annotations

import hashlib

from runtime.context import (
    ContextSemanticModel,
    ContextUnit,
    ShadowClassifier,
    ShadowComparisonBuilder,
    ShadowCoverageGate,
)


def _unit(
    unit_id: str,
    context_class: str,
    *,
    authority: str = "APPROVED_PLAN",
    revision: int = 7,
    size: int = 100,
    suffix: str = "",
    role_scope: str = "generator",
) -> ContextUnit:
    content_hash = hashlib.sha256((unit_id + suffix).encode("utf-8")).hexdigest()
    return ContextUnit(
        id=unit_id,
        context_class=context_class,
        authority=authority,
        source_ref=unit_id,
        source_hash=content_hash,
        exact_locator=unit_id,
        project_revision=revision,
        role_scope=role_scope,
        reason="test provenance",
        dependency_path=(unit_id,),
        delivery_mode="INLINE",
        size=size,
    )


def _semantic(*, global_constraints=()):
    return ContextSemanticModel(
        task_identity="task-012",
        role="generator",
        project_revision=7,
        dependency_roots={"requirements": ("REQ-007",)},
        constraints={
            "global_constraints": tuple(global_constraints),
            "task_constraints": (),
            "approval_constraints": (),
            "safety_constraints": (),
        },
        context_classes={
            "mandatory": (_unit("REQ-007", "mandatory"),),
            "task_relevant": (_unit("AC-014", "task_relevant"),),
            "on_demand": (),
            "omitted": (),
            "unknown": (),
        },
        model_hash="0" * 64,
    )


def test_global_constraint_without_direct_graph_edge_is_still_mandatory() -> None:
    semantic = _semantic(global_constraints=("SECURITY-002",))
    units = [
        _unit("REQ-007", "mandatory"),
        _unit("AC-014", "task_relevant"),
    ]
    classification = ShadowClassifier().classify(
        semantic,
        units,
        dependency_edges=[
            {
                "source": "REQ-007",
                "target": "AC-014",
                "confidence": "explicit",
            },
            {
                "source": "TASK-012",
                "target": "SECURITY-002",
                "confidence": "unknown",
            },
        ],
    )
    comparison = ShadowComparisonBuilder().compare(classification, units, units)
    gate = ShadowCoverageGate().evaluate(comparison)

    assert "SECURITY-002" in classification.mandatory_ids
    assert "SECURITY-002" in classification.unknown_ids
    assert "SECURITY-002" in comparison.mandatory_missing
    assert gate.result == "FAIL"
    assert gate.fallback_to_f13 is True


def test_shadow_candidate_can_show_reduction_only_when_mandatory_is_complete() -> None:
    semantic = _semantic()
    mandatory = _unit("REQ-007", "mandatory", size=100)
    task = _unit("AC-014", "task_relevant", size=500)
    optional = _unit("OPTIONAL", "on_demand", size=200)
    current = [mandatory, task, optional]
    classification = ShadowClassifier().classify(semantic, current)
    candidate = [mandatory]
    comparison = ShadowComparisonBuilder().compare(classification, current, candidate)
    gate = ShadowCoverageGate().evaluate(comparison)

    assert comparison.candidate_context_bytes == 100
    assert comparison.current_context_bytes == 800
    assert comparison.potential_reduction_bytes == 700
    assert comparison.mandatory_complete is True
    assert gate.result == "PASS"


def test_stale_and_hash_conflict_never_pass_coverage() -> None:
    semantic = _semantic()
    current = [_unit("REQ-007", "mandatory")]
    classification = ShadowClassifier().classify(semantic, current)
    stale = [_unit("REQ-007", "mandatory", revision=6)]
    stale_result = ShadowComparisonBuilder().compare(classification, current, stale)
    assert ShadowCoverageGate().evaluate(stale_result).result == "FAIL"
    assert stale_result.mandatory_stale == ("REQ-007",)

    changed = [_unit("REQ-007", "mandatory", suffix="changed")]
    conflict_result = ShadowComparisonBuilder().compare(classification, current, changed)
    assert ShadowCoverageGate().evaluate(conflict_result).result == "FAIL"
    assert conflict_result.mandatory_conflict == ("REQ-007",)
