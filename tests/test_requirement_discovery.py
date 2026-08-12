"""Research-Guided Adaptive Requirement Discovery 的协议与 Runtime 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.errors import RuntimeValidationError
from runtime.intake.first_ask import FirstAskIntakeModule
from runtime.orchestrator import Orchestrator
from runtime.requirements_discovery import (
    DiscoveryArtifactStore,
    DomainResearchModule,
    analyze_gaps,
    analyze_initial_intent,
    apply_question_answers,
    build_coverage_map,
    build_research_queries,
    empty_requirements_snapshot,
    evaluate_research_necessity,
    evaluate_sufficiency,
    prioritize_questions,
    run_discovery_cycle,
)
from runtime.session_store import SessionStore
from runtime.control_plane import initialize_control_plane
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state, validate_project_state
from tests.test_project_migration import v4_state


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "test_requirement_discovery_project"
    root.mkdir()
    state = v4_state("INTAKE")
    state["project_id"] = "test_requirement_discovery"
    control_home = tmp_path / "control-plane"
    session = SessionStore(
        initialize_control_plane(state["project_id"], home=control_home) / "sessions.sqlite3"
    ).create_session(state["project_id"], root, idempotency_key="discovery-session")
    preview = preview_runtime_migration(state, project_root=root, session_id=session.session_id)
    (root / "project.yaml").write_text(serialize_project_state(preview), encoding="utf-8")
    assert validate_project_state(load_project_state(root / "project.yaml"), root) == []
    return root, control_home


def _snapshot(text: str = "做一个业务系统") -> dict[str, object]:
    return empty_requirements_snapshot(
        "test_requirement_discovery",
        version=1,
        request_ref="memory/requirements/reference-request-001.md",
        user_text=text,
        created_at="2026-01-01T00:00:00Z",
    )


def test_t01_initial_intent_separates_fact_candidate_and_unknown() -> None:
    intent = analyze_initial_intent("做一个医疗预约管理系统", project_id="p", source_request_ref="r")
    assert intent["known_facts"][0]["epistemic_status"] == "fact"
    assert intent["domain"]["epistemic_status"] == "candidate"
    assert "platform" in intent["unknown_facts"]


def test_t02_research_gate_is_deterministic_and_required_for_regulated_domain() -> None:
    intent = analyze_initial_intent("做一个医疗预约管理系统", project_id="p", source_request_ref="r")
    first = evaluate_research_necessity(intent, user_text="做一个医疗预约管理系统")
    second = evaluate_research_necessity(intent, user_text="做一个医疗预约管理系统")
    assert first == second
    assert first["decision"] == "required"


def test_t03_simple_tool_can_skip_research() -> None:
    intent = analyze_initial_intent("做一个简单倒计时工具", project_id="p", source_request_ref="r")
    assert evaluate_research_necessity(intent, user_text="做一个简单倒计时工具")["decision"] == "not_required"


def test_t04_query_builder_uses_abstract_domain_only() -> None:
    queries = build_research_queries({"domain": {"value": "healthcare"}})
    assert queries
    assert all("医疗" in query or "healthcare" in query for query in queries)


def test_t05_query_builder_rejects_secret_like_query() -> None:
    from runtime.requirements_discovery.research import _safe_query

    with pytest.raises(RuntimeValidationError):
        _safe_query("healthcare api_key=secret-value")


def test_t06_coverage_map_keeps_all_dimensions() -> None:
    coverage = build_coverage_map(
        _snapshot(),
        project_id="p",
        requirements_ref="requirements_v001.yaml",
    )
    keys = {item["key"] for item in coverage["items"]}
    assert {"product_intent", "platform", "data_model", "security_privacy"} <= keys


def test_t07_gap_analysis_prioritizes_critical_unknowns() -> None:
    snapshot = _snapshot()
    coverage = build_coverage_map(snapshot, project_id="p", requirements_ref="r")
    gaps = analyze_gaps(coverage, project_id="p")
    assert gaps["gaps"][0]["priority"] == "critical"
    assert gaps["gaps"][0]["question_allowed"] is True


def test_t08_question_set_has_at_most_three_high_value_questions() -> None:
    cycle = run_discovery_cycle(
        _snapshot(),
        project_id="p",
        requirements_ref="r",
        request_text="做一个业务系统",
        round_number=1,
    )
    assert 0 <= len(cycle["question_set"]["questions"]) <= 3
    assert len({item["question_key"] for item in cycle["question_set"]["questions"]}) == len(cycle["question_set"]["questions"])


def test_t09_answer_round_reranks_and_marks_source() -> None:
    snapshot = _snapshot()
    cycle = run_discovery_cycle(snapshot, project_id="p", requirements_ref="r", request_text="做一个业务系统", round_number=1)
    updated = apply_question_answers(snapshot, cycle["question_set"], "Web；团队使用；需要登录", interview_ref="interview-001")
    assert any(field.get("source_refs") for field in updated.values() if isinstance(field, dict) and field.get("last_question_key"))
    next_cycle = run_discovery_cycle(updated, project_id="p", requirements_ref="r2", request_text="做一个业务系统", round_number=2)
    assert len(next_cycle["question_set"]["questions"]) <= 3


def test_t10_question_history_does_not_repeat_answered_question() -> None:
    snapshot = _snapshot()
    cycle = run_discovery_cycle(snapshot, project_id="p", requirements_ref="r", request_text="做一个业务系统", round_number=1)
    updated = apply_question_answers(snapshot, cycle["question_set"], "Web", interview_ref="interview-001")
    next_cycle = run_discovery_cycle(updated, project_id="p", requirements_ref="r2", request_text="做一个业务系统", round_number=2)
    old_keys = {question["question_key"] for question in cycle["question_set"]["questions"]}
    new_keys = {question["question_key"] for question in next_cycle["question_set"]["questions"]}
    assert not (old_keys & new_keys)


def test_t11_sufficiency_gate_blocks_unsafe_assumptions() -> None:
    snapshot = _snapshot()
    coverage = build_coverage_map(snapshot, project_id="p", requirements_ref="r", intent={"risk_level": "regulated", "likely_complexity": "deep"})
    result = evaluate_sufficiency(coverage, project_id="p", snapshot_material=snapshot, research_decision="required", research_status="unavailable", risk_level="regulated")
    assert result["decision"] in {"blocked", "waiting_user"}
    assert result["blocking_items"]


def test_t12_visual_undecided_is_a_design_route_not_a_requirement() -> None:
    snapshot = _snapshot()
    snapshot["design_preferences"]["status"] = "undecided"  # type: ignore[index]
    coverage = build_coverage_map(snapshot, project_id="p", requirements_ref="r")
    result = evaluate_sufficiency(coverage, project_id="p", snapshot_material=snapshot)
    assert "design_preferences" in result["routed_to_design_exploration"]


def test_t13_artifact_store_is_append_only(tmp_path: Path) -> None:
    store = DiscoveryArtifactStore(tmp_path, actor="first_ask_intake")
    store.write("memory/requirements/intent-analysis-001.yaml", {"schema_version": 1})
    with pytest.raises(RuntimeValidationError):
        store.write("memory/requirements/intent-analysis-001.yaml", {"schema_version": 2})


def test_t14_schema_inventory_contains_discovery_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = {"intent_analysis_v1.schema.json", "research_plan_v1.schema.json", "coverage_map_v1.schema.json", "sufficiency_evaluation_v1.schema.json"}
    assert expected <= {path.name for path in (root / "config" / "schemas").glob("*discovery*.json")} | {path.name for path in (root / "config" / "schemas").glob("*.json")}
    for path in (root / "config" / "schemas").glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_t15_first_ask_routes_research_without_fourth_agent(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    result = FirstAskIntakeModule(root, orchestrator=runtime).process_user_message("做一个医疗预约管理系统", worker_id="discovery-first-ask")
    state = load_project_state(root / "project.yaml")
    assert result.route == "REQUIREMENT_RESEARCH"
    assert state["active_module"] == "domain_research"
    assert state["next_role"] is None
    assert state["requirements_discovery_status"] == "researching"


def test_t16_unavailable_research_returns_to_intake_with_explicit_status(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    FirstAskIntakeModule(root, orchestrator=runtime).process_user_message("做一个医疗预约管理系统", worker_id="discovery-first-ask")
    outcome = DomainResearchModule(root, orchestrator=runtime).run(worker_id="discovery-research")
    state = load_project_state(root / "project.yaml")
    assert outcome.status == "unavailable"
    assert state["status"] == "INTAKE"
    assert state["research_status"] == "unavailable"
    assert Path(root / state["active_research_round"]).is_file()


def test_t17_successful_adapter_writes_sources_and_findings(tmp_path: Path) -> None:
    class Adapter:
        def search(self, query: str, *, max_results: int):
            return [{
                "source_type": "professional_source",
                "locator": "https://example.com/healthcare-workflow",
                "title": "Workflow",
                "supported_claims": ["workflow claim"],
                "findings": [{"epistemic_status": "PATTERN", "domain": "healthcare", "claim": "预约产品通常需要角色分工", "confidence": "medium", "requirement_effect": "candidate_supports"}],
            }]

    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    FirstAskIntakeModule(root, orchestrator=runtime).process_user_message("做一个医疗预约管理系统", worker_id="discovery-first-ask")
    outcome = DomainResearchModule(root, orchestrator=runtime, adapter=Adapter()).run(worker_id="discovery-research")
    assert outcome.status == "completed"
    assert len(outcome.source_refs) == 1
    assert len(outcome.finding_refs) == 1
    assert "trust_boundary" in yaml.safe_load((root / outcome.summary_ref).read_text(encoding="utf-8"))


def test_t18_domain_research_context_is_policy_bound(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    FirstAskIntakeModule(root, orchestrator=runtime).process_user_message("做一个医疗预约管理系统", worker_id="discovery-first-ask")
    started = runtime.start(worker_id="discovery-context")
    assert started["selection"].target == "domain_research"
    package = ContextBuilder(runtime.store).build(ContextBuildRequest(str(started["session_id"]), "discovery-context-run", "domain_research"))
    assert any(source.source_type == "project_state" for source in package.sources)
    assert all("api_key" not in (source.inline_content or "").lower() for source in package.sources)
