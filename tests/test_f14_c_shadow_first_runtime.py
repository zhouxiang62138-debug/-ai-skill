"""F14-C Shadow-First 编排入口的端到端旁路测试。"""

from __future__ import annotations

from runtime.context import ContextBuildRequest, ContextBuilder, ShadowFirstContextRuntime
from tests.test_f14_c_shadow_coverage import _semantic, _unit
from tests.test_formal_context_builder import _prepare_project


def test_shadow_first_runtime_persists_evidence_without_replacing_f13_package(tmp_path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    request = ContextBuildRequest(session_id, run_id, "generator")
    current = ContextBuilder(store).build(request)
    original_hash = current.context_hash
    mandatory = _unit(
        "REQ-007", "mandatory", revision=current.project_revision, size=100
    )
    optional = _unit(
        "OPTIONAL", "on_demand", revision=current.project_revision, size=400
    )

    result = ShadowFirstContextRuntime(store).evaluate(
        request,
        current,
        current_units=[mandatory, optional],
        candidate_units=[mandatory],
        candidate_id="candidate-001",
    )

    assert current.context_hash == original_hash
    assert result.gate.passed is True
    assert result.candidate is not None and result.candidate.eligible is True
    assert result.semantic_id
    assert result.comparison_id
    assert result.gate_id
    assert result.candidate_id
    connection = store.raw_connection()
    try:
        counts = {
            name: connection.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"]
            for name in (
                "f14_context_semantic_snapshots",
                "f14_shadow_comparisons",
                "f14_shadow_gate_results",
                "f14_selective_candidates",
            )
        }
    finally:
        connection.close()
    assert counts == {
        "f14_context_semantic_snapshots": 1,
        "f14_shadow_comparisons": 1,
        "f14_shadow_gate_results": 1,
        "f14_selective_candidates": 1,
    }
