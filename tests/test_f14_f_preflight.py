"""F14-F0/F1：Feature Flag、基线冻结和 Selective 门禁测试。"""

from runtime.deterministic.benchmark import BaselineSnapshot
from runtime.f14_control import (
    BASELINE_IDS,
    F13_FULL,
    F14_SELECTIVE,
    ContextSavings,
    F14BaselineFreeze,
    F14FeatureFlags,
    F14RolloutPolicy,
    SelectiveContextGate,
    SelectiveGateInput,
)


def _baseline(baseline_id: str) -> BaselineSnapshot:
    return BaselineSnapshot(
        baseline_id=baseline_id,
        code_commit_or_tree_hash="tree",
        runtime_schema=13,
        config_hash="config",
        benchmark_fixture_hash="fixture",
        model_id="f14-g-controlled-fake-model",
        environment_hash="environment",
        evaluation_profile_hash="profile",
        inputs_fingerprint="inputs",
    )


def _flags() -> F14FeatureFlags:
    return F14FeatureFlags(
        context_delivery_mode=F14_SELECTIVE,
        selective_context_enabled=True,
        selective_roles=("generator",),
        selective_phases=("rework",),
        qualification_evidence={"controlled": "PASS"},
        rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
    )


def test_f14_g_baseline_freeze_requires_all_five_ids() -> None:
    frozen = F14BaselineFreeze(tuple(_baseline(item) for item in BASELINE_IDS))
    assert frozen.baseline_ids == BASELINE_IDS
    assert len(frozen.freeze_hash) == 64


def test_feature_flag_defaults_to_f13_and_canary_scope_is_explicit() -> None:
    flags = F14FeatureFlags()
    assert flags.delivery_for(role="generator", phase="rework") == F13_FULL
    assert _flags().delivery_for(role="generator", phase="rework", test_project=True) == F14_SELECTIVE
    assert _flags().delivery_for(role="planner", phase="rework", test_project=True) == F13_FULL


def test_selective_gate_uses_net_context_after_expansion() -> None:
    savings = ContextSavings(1000, 400, expansion_bytes=100, recovery_bytes=50)
    decision = SelectiveContextGate().evaluate(
        flags=_flags(),
        role="generator",
        phase="rework",
        test_project=True,
        savings=savings,
        gate=SelectiveGateInput(True, True, True),
    )
    assert decision.result == F14_SELECTIVE
    assert decision.savings.net_context_bytes == 550
    assert decision.savings.net_reduction_ratio == 0.45


def test_selective_gate_blocks_authoritative_coverage_failure() -> None:
    decision = SelectiveContextGate().evaluate(
        flags=_flags(),
        role="generator",
        phase="rework",
        test_project=True,
        savings=ContextSavings(1000, 500),
        gate=SelectiveGateInput(False, True, True),
    )
    assert decision.result == "BLOCKED"
    assert "mandatory_coverage_incomplete" in decision.reasons


def test_optimization_failure_falls_back_to_f13() -> None:
    decision = SelectiveContextGate().evaluate(
        flags=_flags(),
        role="generator",
        phase="rework",
        test_project=True,
        savings=ContextSavings(1000, 500),
        gate=SelectiveGateInput(True, True, True),
        optimization_error="coverage_engine_unavailable",
    )
    assert decision.result == "FALLBACK_F13"
    assert decision.delivery == F13_FULL
