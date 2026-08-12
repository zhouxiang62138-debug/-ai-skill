"""Production Readiness Stage 3：真实 FAIL → FIX → PASS 返工闭环。"""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.control_plane import initialize_control_plane
from runtime.errors import RuntimeValidationError
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
    LocalCompatibilityEnvironment,
)
from runtime.orchestrator import Orchestrator
from runtime.session_store import SessionStore
from scripts.approval import (
    PLAN_SECTIONS,
    PRODUCT_SPEC_SECTIONS,
    approve_plan,
    classify_approval,
    prepare_product_approval_for_plan_review,
)
from scripts.evaluation_evidence import (
    build_protected_snapshot,
    capture_environment,
    compare_protected_snapshot,
    commit_reproducible_evaluation,
    evaluate_gates,
    required_gates_passed,
    run_verified_command,
    validate_delivery_handoff,
)
from scripts.evaluation_governance import (
    MAXIMUM_AUTOMATIC_ITERATIONS,
    apply_controlled_retry,
    assign_stable_issue_identity,
    build_decision_summary,
    build_verification_plan,
    compute_iteration_metrics,
    issue_signature,
    select_incremental_rechecks,
    update_repeat_history,
    write_decision_summary,
)
from scripts.evaluation_protocol import (
    build_recheck_records,
    evaluate_pass_policy,
    load_issue_package,
    validate_generator_response,
    validate_issue_package,
    write_generator_response_atomic,
)
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from tests.test_approval import (
    CONCEPT,
    FEEDBACK,
    PROPOSAL,
    REQUIREMENTS,
    ROUND,
    SELECTION,
    product_review_state,
)
from tests.test_evaluation_protocol import make_issue, make_package
from tests.runtime_test_support import commit_step_with_test_attestation
from tests.test_stage2_core_e2e import (
    _changed_fields,
    _commit_user_transition,
    _execution_context,
    _render_artifact,
)


PROJECT_ID = "test_stage3_rework_todo_app"
PRODUCT_APPROVAL = "memory/decisions/product-approval-001.md"
PRODUCT_SPEC = "memory/specifications/product_spec_v001.md"
PLAN = "memory/plans/plan-001.md"
PLAN_APPROVAL = "memory/decisions/plan-approval-001.md"
HANDOFF_001 = "memory/handoffs/handoff-001.md"
HANDOFF_002 = "memory/handoffs/handoff-002.md"
ISSUE_001 = "evaluation/issues/evaluation-001.yaml"
ISSUE_002 = "evaluation/issues/evaluation-002.yaml"
REPORT_001 = "evaluation/reports/evaluation-001.md"
REPORT_002 = "evaluation/reports/evaluation-002.md"
MANIFEST_001 = "evaluation/evidence/evaluation-001/manifest.yaml"
MANIFEST_002 = "evaluation/evidence/evaluation-002/manifest.yaml"
RESPONSE_001 = "memory/handoffs/responses/evaluation-001-response.yaml"


def _write(root: Path, reference: str, content: str) -> None:
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_project(tmp_path: Path) -> tuple[Path, SessionStore, Orchestrator]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane(PROJECT_ID, home=control_home)
    store = SessionStore(control_plane / "sessions.sqlite3")
    session = store.create_session(PROJECT_ID, root, idempotency_key="stage3-session")

    state = product_review_state()
    state["project_id"] = PROJECT_ID
    state["evaluation_profile"] = "default"
    runtime_state = preview_runtime_migration(
        state, project_root=root, session_id=session.session_id
    )
    _write(root, REQUIREMENTS, "REQ-001\nREQ-002\nREQ-003\n")
    _write(root, PROPOSAL, "# Todo Web App Product Proposal\n\n最小 Todo Web App。\n")
    _write(root, FEEDBACK, "用户选择并确认设计整合。\n")
    _write(root, SELECTION, "选择 concept_01 与 concept_03 的组合。\n")
    for concept in ("concept_01", "concept_02", "concept_03"):
        _write(root, f"{ROUND}/{concept}/concept.md", f"{concept}\n")
        _write(root, f"{ROUND}/{concept}/preview.html", "<main>Todo</main>\n")
        _write(root, f"{ROUND}/{concept}/preview.css", "main { color: black; }\n")
    (root / "project.yaml").write_text(
        serialize_project_state(runtime_state), encoding="utf-8"
    )
    return root, store, Orchestrator(root, control_plane_home=control_home)


def _advance_to_evaluating(
    root: Path, store: SessionStore, orchestrator: Orchestrator
) -> dict:
    session_id = str(load_project_state(root / "project.yaml")["runtime"]["session_id"])
    product_decision = classify_approval("确认当前产品方案", "product")
    assert product_decision.action == "approve"
    _write(
        root,
        PRODUCT_APPROVAL,
        "\n".join((REQUIREMENTS, PROPOSAL, PRODUCT_SPEC, PLAN, SELECTION)),
    )
    current = load_project_state(root / "project.yaml")
    approved_product = copy.deepcopy(current)
    approved_product.update(
        {
            "status": "PLANNING",
            "next_role": "planner",
            "active_module": None,
            "proposal_status": "approved",
            "user_approval_status": "approved",
            "approved_proposal": current["active_proposal"],
            "product_approval_record": PRODUCT_APPROVAL,
        }
    )
    _commit_user_transition(
        orchestrator,
        root,
        _changed_fields(current, approved_product),
        source_status="WAITING_FOR_PRODUCT_REVIEW",
        target_status="PLANNING",
        worker_id="product-approval-stage3-worker",
        idempotency_key="stage3-product-approval",
    )

    planner_start = orchestrator.start(worker_id="planner-stage3-worker")
    assert planner_start["selection"].target == "planner"
    planner_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, planner_start["run_id"], "planner")
    )
    assert planner_context.workflow_state == "PLANNING"
    assert any(source.reference == PROPOSAL for source in planner_context.sources)
    source_refs = [REQUIREMENTS, PROPOSAL, PRODUCT_APPROVAL, FEEDBACK, SELECTION]
    _write(
        root,
        PRODUCT_SPEC,
        _render_artifact("正式产品规格 v001", PRODUCT_SPEC_SECTIONS, source_refs),
    )
    _write(
        root,
        PLAN,
        _render_artifact(
            "开发计划 plan-001", PLAN_SECTIONS, [*source_refs, PRODUCT_SPEC]
        ),
    )
    current = load_project_state(root / "project.yaml")
    planner_target = prepare_product_approval_for_plan_review(
        product_review_state(),
        product_decision,
        product_approval_record=PRODUCT_APPROVAL,
        product_spec_reference=PRODUCT_SPEC,
        plan_reference=PLAN,
        project_root=root,
    )
    planner_target["project_id"] = PROJECT_ID
    planner_target["runtime"] = current["runtime"]
    planner_target["schema_version"] = 7
    commit_step_with_test_attestation(orchestrator,
        session_id,
        planner_start["run_id"],
        planner_start["lease_token"],
        {
            "source_status": "PLANNING",
            "target_status": "WAITING_FOR_PLAN_REVIEW",
            "changed_fields": _changed_fields(current, planner_target),
            "expected_revision": current["runtime"]["revision"],
            "idempotency_key": "stage3-formal-spec-and-plan",
        },
    )

    state = load_project_state(root / "project.yaml")
    plan_decision = classify_approval("确认当前开发 Plan", "plan")
    assert plan_decision.action == "approve"
    _write(
        root,
        PLAN_APPROVAL,
        "\n".join((REQUIREMENTS, PROPOSAL, PRODUCT_APPROVAL, PRODUCT_SPEC, PLAN, SELECTION)),
    )
    plan_target = approve_plan(
        state,
        plan_decision,
        plan_approval_record=PLAN_APPROVAL,
        project_root=root,
    )
    _commit_user_transition(
        orchestrator,
        root,
        _changed_fields(state, plan_target),
        source_status="WAITING_FOR_PLAN_REVIEW",
        target_status="APPROVED_FOR_IMPLEMENTATION",
        worker_id="plan-approval-stage3-worker",
        idempotency_key="stage3-plan-approval",
    )

    state = load_project_state(root / "project.yaml")
    generator_gate_start = orchestrator.start(worker_id="generator-stage3-gate-worker")
    assert generator_gate_start["selection"].target == "generator"
    commit_step_with_test_attestation(orchestrator,
        session_id,
        generator_gate_start["run_id"],
        generator_gate_start["lease_token"],
        {
            "source_status": "APPROVED_FOR_IMPLEMENTATION",
            "target_status": "IMPLEMENTING",
            "changed_fields": {
                "status": "IMPLEMENTING",
                "next_role": "generator",
                "active_module": None,
            },
            "expected_revision": state["runtime"]["revision"],
            "idempotency_key": "stage3-enter-implementing",
        },
    )

    generator_start = orchestrator.start(worker_id="generator-stage3-worker")
    assert generator_start["selection"].target == "generator"
    generator_context = ContextBuilder(store).build_resume(
        ContextBuildRequest(session_id, generator_start["run_id"], "generator")
    )
    assert generator_context.resume_mode == "FULL_BUILD"
    assert any(source.reference == PLAN for source in generator_context.context.sources)
    generator_runtime = _execution_context(root, generator_start, "generator")
    environment = LocalCompatibilityEnvironment()
    broker = ExecutionBroker(store, orchestrator.leases)
    broker.provision(generator_runtime, environment, lease_token=generator_start["lease_token"])
    with pytest.raises(RuntimeValidationError, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        broker.write_file(
            generator_runtime,
            environment,
            "project.yaml",
            "status: ACCEPTED\n",
            lease_token=generator_start["lease_token"],
        )

    buggy_code = """from dataclasses import dataclass


@dataclass
class TodoList:
    items: list[str]

    def add(self, title: str) -> None:
        self.items.append(title)

    def delete(self, title: str) -> None:
        return None

    def list(self) -> list[str]:
        return list(self.items)
"""
    initial_tests = """import unittest

from todo_app import TodoList


class TodoListTests(unittest.TestCase):
    def test_add_and_list(self) -> None:
        todos = TodoList([])
        todos.add("write test")
        self.assertEqual(["write test"], todos.list())


if __name__ == "__main__":
    unittest.main()
"""
    broker.write_file(
        generator_runtime,
        environment,
        "code/todo_app.py",
        buggy_code,
        lease_token=generator_start["lease_token"],
    )
    broker.write_file(
        generator_runtime,
        environment,
        "code/test_todo.py",
        initial_tests,
        lease_token=generator_start["lease_token"],
    )
    generator_test_command = ExecutionRequest(
        logical_call_id="generator-stage3-self-test",
        argv=("python", "-m", "unittest", "discover", "-s", "code", "-p", "test_*.py"),
        execution_profile=ExecutionProfile("compatibility", "a" * 64, "b" * 64),
        timeout=30,
    )
    generator_receipt = broker.execute(
        generator_runtime,
        generator_test_command,
        environment,
        lease_token=generator_start["lease_token"],
    )
    assert generator_receipt.status == "SUCCEEDED"
    _write(
        root,
        HANDOFF_001,
        "\n".join(
            (
                "# Generator Handoff handoff-001",
                "",
                "实现 TodoList 新增与列表展示；删除功能交由验收验证。",
                f"- code/todo_app.py sha256: {hashlib.sha256(buggy_code.encode()).hexdigest()}",
                f"- code/test_todo.py sha256: {hashlib.sha256(initial_tests.encode()).hexdigest()}",
                "- 自测：python -m unittest discover -s code -p test_*.py，退出码 0。",
            )
        ),
    )
    current = load_project_state(root / "project.yaml")
    evaluating_target = copy.deepcopy(current)
    evaluating_target.update(
        {
            "status": "EVALUATING",
            "next_role": "evaluator",
            "active_module": None,
            "last_generator_response": HANDOFF_001,
        }
    )
    commit_step_with_test_attestation(orchestrator,
        session_id,
        generator_start["run_id"],
        generator_start["lease_token"],
        {
            "source_status": "IMPLEMENTING",
            "target_status": "EVALUATING",
            "changed_fields": _changed_fields(current, evaluating_target),
            "expected_revision": current["runtime"]["revision"],
            "idempotency_key": "stage3-generator-handoff-001",
        },
    )
    return {
        "session_id": session_id,
        "generator_start": generator_start,
        "buggy_code": buggy_code,
        "initial_tests": initial_tests,
    }


def _gate_policy() -> list[dict[str, object]]:
    return [
        {"id": "GATE-DELIVERY", "required": True},
        {"id": "GATE-BUILD", "required": False},
        {"id": "GATE-TESTS", "required": True},
        {"id": "GATE-REQUIREMENTS", "required": True},
        {"id": "GATE-REGRESSION", "required": True},
        {"id": "GATE-NON_FUNCTIONAL", "required": False},
        {"id": "GATE-EVIDENCE", "required": True},
    ]


def _manifest(
    root: Path,
    evaluation_id: str,
    commands: list[dict],
    handoff: str,
    response_reference: str | None,
    issue_id: str | None,
    *,
    delete_result: str,
) -> dict:
    linked_issue_ids = [issue_id] if issue_id else []
    artifacts = [
        {
            "artifact_id": "ART-001",
            "type": "other",
            "path": handoff,
            "linked_issue_ids": linked_issue_ids,
            "linked_requirement_ids": ["REQ-001", "REQ-002", "REQ-003"],
        },
        {
            "artifact_id": "ART-002",
            "type": "other",
            "path": "code/todo_app.py",
            "linked_issue_ids": linked_issue_ids,
            "linked_requirement_ids": ["REQ-001", "REQ-002", "REQ-003"],
        },
    ]
    if response_reference:
        artifacts.append(
            {
                "artifact_id": "ART-003",
                "type": "other",
                "path": response_reference,
                "linked_issue_ids": linked_issue_ids,
                "linked_requirement_ids": ["REQ-003"],
            }
        )
    checks = [
        {
            "check_id": "CHECK-001",
            "gate_id": "GATE-REQUIREMENTS",
            "required": True,
            "requirement_id": "REQ-001",
            "acceptance_criterion_id": "AC-REQ-001",
            "result": "PASS",
            "evidence_refs": ["CMD-002", "ART-002"],
        },
        {
            "check_id": "CHECK-002",
            "gate_id": "GATE-REQUIREMENTS",
            "required": True,
            "requirement_id": "REQ-002",
            "acceptance_criterion_id": "AC-REQ-002",
            "result": "PASS",
            "evidence_refs": ["CMD-002", "ART-002"],
        },
        {
            "check_id": "CHECK-003",
            "gate_id": "GATE-REQUIREMENTS",
            "required": True,
            "requirement_id": "REQ-003",
            "acceptance_criterion_id": "AC-REQ-003",
            "result": delete_result,
            "evidence_refs": ["CMD-001", "ART-002"],
        },
    ]
    gates = evaluate_gates(
        {
            "GATE-DELIVERY": {"result": "PASS", "evidence_refs": ["ART-001"]},
            "GATE-TESTS": {
                "result": "PASS" if delete_result == "PASS" else "FAIL",
                "evidence_refs": ["CMD-002" if delete_result == "PASS" else "CMD-001"],
            },
            "GATE-REQUIREMENTS": {
                "result": "PASS" if delete_result == "PASS" else "FAIL",
                "evidence_refs": ["CHECK-003"],
            },
            "GATE-REGRESSION": {"result": "PASS", "evidence_refs": ["CMD-002"]},
            "GATE-EVIDENCE": {"result": "PASS", "evidence_refs": ["ART-001", "ART-002"]},
        },
        _gate_policy(),
    )
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": evaluation_id,
        "created_at": "2026-08-08T00:00:00+00:00",
        "environment": capture_environment("code"),
        "commands": commands,
        "artifacts": artifacts,
        "checks": checks,
        "gates": gates,
    }
    assert root.is_dir()
    return manifest


def _cas_evaluation_commit(
    root: Path,
    orchestrator: Orchestrator,
    evaluator_start: dict,
    next_state: dict,
    package: dict,
    manifest: dict,
    *,
    idempotency_key: str,
) -> dict[str, str]:
    session_id = evaluator_start["session_id"]
    lease = orchestrator.leases.get(session_id)
    cas_result: dict[str, dict] = {}

    def state_writer(path: str | Path, candidate: dict) -> None:
        current = load_project_state(path)
        result = orchestrator.cas.commit_patch(
            path,
            _changed_fields(current, candidate),
            source_status="EVALUATING",
            target_status=str(candidate["status"]),
            session_id=session_id,
            worker_id=evaluator_start["worker_id"],
            actor_role="evaluator",
            lease_version=lease.lease_version,
            lease_token=evaluator_start["lease_token"],
            expected_revision=current["runtime"]["revision"],
            idempotency_key=idempotency_key,
        )
        cas_result["value"] = result

    result = commit_reproducible_evaluation(
        root,
        package,
        manifest,
        next_state,
        state_writer=state_writer,
    )
    orchestrator.store.complete_role_run(
        session_id,
        evaluator_start["run_id"],
        cas_result["value"],
    )
    orchestrator.leases.release(
        session_id,
        evaluator_start["worker_id"],
        lease.lease_version,
        evaluator_start["lease_token"],
    )
    return result


def test_stage3_real_fail_fix_pass_rework(tmp_path: Path) -> None:
    root, store, orchestrator = _seed_project(tmp_path)
    setup = _advance_to_evaluating(root, store, orchestrator)
    session_id = setup["session_id"]

    evaluator_start = orchestrator.start(worker_id="evaluator-stage3-first-worker")
    assert evaluator_start["selection"].target == "evaluator"
    evaluator_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, evaluator_start["run_id"], "evaluator")
    )
    assert any(source.reference == PLAN for source in evaluator_context.sources)
    assert any(source.reference == HANDOFF_001 for source in evaluator_context.sources)
    evaluator_runtime = _execution_context(root, evaluator_start, "evaluator")
    evaluator_environment = LocalCompatibilityEnvironment()
    evaluator_broker = ExecutionBroker(store, orchestrator.leases)
    evaluator_broker.provision(
        evaluator_runtime,
        evaluator_environment,
        lease_token=evaluator_start["lease_token"],
    )
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_PROHIBITED"):
        evaluator_broker.write_file(
            evaluator_runtime,
            evaluator_environment,
            "code/todo_app.py",
            "unauthorized",
            lease_token=evaluator_start["lease_token"],
        )

    failing_command = [
        "python",
        "-c",
        "from todo_app import TodoList; todos = TodoList(['remove me']); todos.delete('remove me'); assert todos.list() == [], 'delete did not remove item'",
    ]
    first_test = run_verified_command(
        root,
        "evaluation-001",
        "CMD-001",
        "GATE-TESTS",
        failing_command,
        allowed_prefixes=[["python", "-c"]],
        cwd="code",
        test_metrics={
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "failed_tests": ["TodoListTests.test_delete"],
        },
    )
    first_regression = run_verified_command(
        root,
        "evaluation-001",
        "CMD-002",
        "GATE-REGRESSION",
        ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
        allowed_prefixes=[["python", "-m", "unittest"]],
        cwd="code",
    )
    assert first_test["status"] == "FAILED"
    assert first_test["exit_code"] != 0
    assert first_regression["status"] == "PASSED"
    stderr = (root / first_test["stderr_path"]).read_text(encoding="utf-8")
    assert "delete did not remove item" in stderr

    issue1 = make_issue(
        current_id="EVAL-001-001",
        category="implementation_defect",
        severity="critical",
        status="OPEN",
        route_to="GENERATOR",
    )
    issue1.update(
        {
            "title": "Todo 删除行为未移除任务",
            "requirement_id": "REQ-003",
            "acceptance_criterion_id": "AC-REQ-003",
            "expected_result": "删除任务后列表不再包含该任务",
            "actual_result": "delete 调用后任务仍然存在",
            "reproduction_steps": ["进入 code 目录", "执行 CMD-001 删除验收命令"],
            "evidence_refs": [MANIFEST_001, "CMD-001"],
            "affected_scope": ["code/todo_app.py"],
            "allowed_scope": ["code/todo_app.py", "code/test_todo.py"],
            "forbidden_changes": [PLAN, "config/evaluation_rules/default.yaml"],
            "verification_commands": [failing_command],
            "root_cause_key": "implementation_defect|REQ-003|AC-REQ-003|todo-delete-no-op",
            "related_test_ids": ["TodoListTests.test_delete"],
        }
    )
    package1 = make_package(issue1)
    package1.update(
        {
            "evaluation_id": "evaluation-001",
            "project_id": PROJECT_ID,
            "current_iteration": 0,
            "report_reference": REPORT_001,
            "previous_evaluation": None,
        }
    )
    assert validate_issue_package(package1) == []
    gates1 = _manifest(
        root,
        "evaluation-001",
        [first_test, first_regression],
        HANDOFF_001,
        None,
        issue1["issue_id"],
        delete_result="FAIL",
    )
    gates_passed, gate_failures = required_gates_passed(gates1["gates"])
    assert not gates_passed
    assert "GATE-TESTS=FAIL" in gate_failures
    policy_passed, policy_reasons = evaluate_pass_policy(
        package1,
        required_acceptance_criteria_checked=False,
        required_generator_handoff_present=True,
        required_evidence_complete=True,
        protected_artifacts_unchanged=True,
    )
    assert not policy_passed
    assert policy_reasons

    protected_before = build_protected_snapshot(
        root,
        [
            "memory/plans",
            "memory/specifications",
            "memory/decisions",
            "config/evaluation_rules",
            "config/schemas",
        ],
    )
    fail_state = apply_controlled_retry(
        load_project_state(root / "project.yaml"), package1
    )
    assert fail_state["status"] == "IMPLEMENTING"
    assert fail_state["next_role"] == "generator"
    assert fail_state["current_iteration"] == 1
    _cas_evaluation_commit(
        root,
        orchestrator,
        evaluator_start,
        fail_state,
        package1,
        gates1,
        idempotency_key="stage3-evaluation-001-fail",
    )
    failed_state = load_project_state(root / "project.yaml")
    assert failed_state["status"] == "IMPLEMENTING"
    assert failed_state["next_role"] == "generator"
    assert failed_state["current_iteration"] == 1
    assert failed_state["last_evaluation"] == REPORT_001
    assert failed_state["last_issue_package"] == ISSUE_001
    assert failed_state["evidence_manifest"] == MANIFEST_001
    assert (root / ISSUE_001).is_file()
    assert (root / REPORT_001).is_file()
    loaded_issue1 = load_issue_package(root, ISSUE_001)
    assert loaded_issue1["result"] == "FAIL"
    assert loaded_issue1["return_to"] == "GENERATOR"
    assert loaded_issue1["issues"][0]["status"] == "OPEN"

    metrics1 = compute_iteration_metrics(None, loaded_issue1)
    repeat1 = update_repeat_history([], loaded_issue1, stalled_threshold=2)
    decision1 = write_decision_summary(
        root,
        build_decision_summary(
            failed_state,
            [loaded_issue1],
            [metrics1],
            repeat1,
            [],
            [],
            reason="implementation_defect_routed_to_generator",
        ),
    )
    assert decision1.is_file()

    generator_fix_start = orchestrator.start(worker_id="generator-stage3-fix-worker")
    assert generator_fix_start["selection"].target == "generator"
    generator_fix_context = ContextBuilder(store).build_resume(
        ContextBuildRequest(
            session_id,
            generator_fix_start["run_id"],
            "generator",
            additional_references=("code/todo_app.py",),
        )
    )
    assert generator_fix_context.resume_mode == "INCREMENTAL"
    deltas = (
        generator_fix_context.added_sources
        + generator_fix_context.modified_sources
    )
    delta_refs = {delta.reference for delta in deltas}
    assert ISSUE_001 in delta_refs
    assert "code/todo_app.py" in delta_refs
    assert generator_fix_context.context.project_id == PROJECT_ID
    assert generator_fix_context.context.session_id == session_id
    assert generator_fix_context.context.role == "generator"
    assert "secret" not in str(generator_fix_context.context.manifest).lower()

    fixed_code = setup["buggy_code"].replace(
        "    def delete(self, title: str) -> None:\n        return None",
        "    def delete(self, title: str) -> None:\n        self.items.remove(title)",
    )
    fixed_tests = setup["initial_tests"].replace(
        "    def test_add_and_list(self) -> None:\n",
        "    def test_delete(self) -> None:\n        todos = TodoList(['remove me'])\n        todos.delete('remove me')\n        self.assertEqual([], todos.list())\n\n    def test_add_and_list(self) -> None:\n",
    )
    generator_fix_runtime = _execution_context(root, generator_fix_start, "generator")
    fix_environment = LocalCompatibilityEnvironment()
    fix_broker = ExecutionBroker(store, orchestrator.leases)
    fix_broker.provision(
        generator_fix_runtime,
        fix_environment,
        lease_token=generator_fix_start["lease_token"],
    )
    with pytest.raises(RuntimeValidationError, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        fix_broker.write_file(
            generator_fix_runtime,
            fix_environment,
            "project.yaml",
            "status: ACCEPTED\n",
            lease_token=generator_fix_start["lease_token"],
        )
    fix_broker.write_file(
        generator_fix_runtime,
        fix_environment,
        "code/todo_app.py",
        fixed_code,
        lease_token=generator_fix_start["lease_token"],
    )
    fix_broker.write_file(
        generator_fix_runtime,
        fix_environment,
        "code/test_todo.py",
        fixed_tests,
        lease_token=generator_fix_start["lease_token"],
    )
    fix_receipt = fix_broker.execute(
        generator_fix_runtime,
        ExecutionRequest(
            logical_call_id="generator-stage3-fix-test",
            argv=("python", "-m", "unittest", "discover", "-s", "code", "-p", "test_*.py"),
            execution_profile=ExecutionProfile("compatibility", "c" * 64, "d" * 64),
            timeout=30,
        ),
        fix_environment,
        lease_token=generator_fix_start["lease_token"],
    )
    assert fix_receipt.status == "SUCCEEDED"

    generator_response = {
        "schema_version": "1.0",
        "source_evaluation": "evaluation-001",
        "generator_handoff_id": "handoff-002",
        "response_reference": RESPONSE_001,
        "created_at": "2026-08-08T00:10:00+00:00",
        "issue_responses": [
            {
                "issue_id": issue1["issue_id"],
                "status": "FIXED",
                "changed_files": ["code/todo_app.py", "code/test_todo.py"],
                "explanation": "修复 delete 实现并补充删除验收测试。",
                "verification_commands": [
                    ["python", "-m", "unittest", "discover", "-s", "code", "-p", "test_*.py"]
                ],
                "verification_results": [
                    {
                        "command": [
                            "python",
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "code",
                            "-p",
                            "test_*.py",
                        ],
                        "exit_code": 0,
                    }
                ],
            }
        ],
    }
    assert validate_generator_response(generator_response, loaded_issue1) == []
    response_path = write_generator_response_atomic(root, generator_response, loaded_issue1)
    assert response_path.as_posix().endswith(RESPONSE_001)
    selected = select_incremental_rechecks(
        loaded_issue1,
        generator_response,
        ["code/todo_app.py", "code/test_todo.py"],
        {
            "AC-REQ-001": ["code/todo_app.py"],
            "AC-REQ-002": ["code/todo_app.py"],
            "AC-REQ-003": ["code/todo_app.py", "code/test_todo.py"],
        },
    )
    assert selected["issue_ids"] == [issue1["issue_id"]]
    assert "AC-REQ-003" in selected["acceptance_criterion_ids"]
    verification_plan = build_verification_plan(
        selected,
        [
            "core_unit_tests",
            "critical_acceptance_criteria",
            "previously_passed_critical_requirements",
        ],
    )
    assert verification_plan["mandatory_regression_may_be_skipped"] is False
    _write(
        root,
        HANDOFF_002,
        "\n".join(
            (
                "# Generator Handoff handoff-002",
                "",
                "修复 TodoList.delete，并补充可执行的删除测试。",
                f"- code/todo_app.py sha256: {hashlib.sha256(fixed_code.encode()).hexdigest()}",
                f"- code/test_todo.py sha256: {hashlib.sha256(fixed_tests.encode()).hexdigest()}",
                f"- generator response: {RESPONSE_001}",
                "- 修复测试：python -m unittest discover -s code -p test_*.py，退出码 0。",
            )
        ),
    )
    handoff_validation = {
        "implementation_summary": "修复 TodoList.delete 并补充测试。",
        "changed_files": ["code/todo_app.py", "code/test_todo.py"],
        "test_results": ["python -m unittest discover -s code -p test_*.py: 0"],
        "known_limitations": [],
        "unfinished_items": [],
        "run_instructions": ["python -m unittest discover -s code -p test_*.py"],
        "handoff_id": "handoff-002",
        "generator_response_reference": RESPONSE_001,
    }
    assert validate_delivery_handoff(
        handoff_validation, previous_issue_package=loaded_issue1
    ) == []

    current = load_project_state(root / "project.yaml")
    evaluating_target = copy.deepcopy(current)
    evaluating_target.update(
        {
            "status": "EVALUATING",
            "next_role": "evaluator",
            "active_module": None,
            "last_generator_response": HANDOFF_002,
        }
    )
    commit_step_with_test_attestation(orchestrator,
        session_id,
        generator_fix_start["run_id"],
        generator_fix_start["lease_token"],
        {
            "source_status": "IMPLEMENTING",
            "target_status": "EVALUATING",
            "changed_fields": _changed_fields(current, evaluating_target),
            "expected_revision": current["runtime"]["revision"],
            "idempotency_key": "stage3-generator-handoff-002",
        },
    )

    evaluator_second_start = orchestrator.start(worker_id="evaluator-stage3-second-worker")
    assert evaluator_second_start["selection"].target == "evaluator"
    evaluator_second_context = ContextBuilder(store).build(
        ContextBuildRequest(
            session_id,
            evaluator_second_start["run_id"],
            "evaluator",
            additional_references=(HANDOFF_002,),
        )
    )
    assert any(source.reference == PLAN for source in evaluator_second_context.sources)
    assert any(source.reference == HANDOFF_002 for source in evaluator_second_context.sources)
    assert any(source.reference == REPORT_001 for source in evaluator_second_context.sources)
    second_runtime = _execution_context(root, evaluator_second_start, "evaluator")
    second_environment = LocalCompatibilityEnvironment()
    second_broker = ExecutionBroker(store, orchestrator.leases)
    second_broker.provision(
        second_runtime,
        second_environment,
        lease_token=evaluator_second_start["lease_token"],
    )
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_PROHIBITED"):
        second_broker.write_file(
            second_runtime,
            second_environment,
            "code/todo_app.py",
            "unauthorized",
            lease_token=evaluator_second_start["lease_token"],
        )

    second_test = run_verified_command(
        root,
        "evaluation-002",
        "CMD-001",
        "GATE-TESTS",
        [
            "python",
            "-c",
            "from todo_app import TodoList; todos = TodoList(['remove me']); todos.delete('remove me'); assert todos.list() == []",
        ],
        allowed_prefixes=[["python", "-c"]],
        cwd="code",
        test_metrics={
            "passed": 1,
            "failed": 0,
            "skipped": 0,
            "failed_tests": [],
        },
    )
    second_regression = run_verified_command(
        root,
        "evaluation-002",
        "CMD-002",
        "GATE-REGRESSION",
        ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
        allowed_prefixes=[["python", "-m", "unittest"]],
        cwd="code",
    )
    assert second_test["status"] == "PASSED"
    assert second_regression["status"] == "PASSED"

    issue2 = copy.deepcopy(issue1)
    issue2.update(
        {
            "status": "RESOLVED",
            "first_seen_evaluation": "evaluation-001",
            "evidence_refs": [MANIFEST_002, "CMD-001"],
            "actual_result": "delete 调用后任务已从列表移除",
            "verification_commands": [
                [
                    "python",
                    "-c",
                    "from todo_app import TodoList; todos = TodoList(['remove me']); todos.delete('remove me'); assert todos.list() == []",
                ]
            ],
        }
    )
    history = [
        {
            "issue_id": issue1["issue_id"],
            "signature": issue_signature(issue1),
            "first_seen_evaluation": "evaluation-001",
            "status": "OPEN",
        }
    ]
    stable_issue = assign_stable_issue_identity(
        {key: value for key, value in issue2.items() if key != "issue_id"},
        history,
        evaluation_id="evaluation-002",
        sequence=1,
    )
    assert stable_issue["issue_id"] == issue1["issue_id"]
    issue2["issue_id"] = stable_issue["issue_id"]
    package2 = make_package(issue2)
    package2.update(
        {
            "evaluation_id": "evaluation-002",
            "project_id": PROJECT_ID,
            "current_iteration": 1,
            "previous_evaluation": "evaluation-001",
            "report_reference": REPORT_002,
        }
    )
    recheck_records = build_recheck_records(
        loaded_issue1,
        generator_response,
        {issue1["issue_id"]: True},
    )
    assert recheck_records[0]["current_result"] == "RESOLVED"
    assert validate_issue_package(package2) == []
    gates2 = _manifest(
        root,
        "evaluation-002",
        [second_test, second_regression],
        HANDOFF_002,
        RESPONSE_001,
        issue1["issue_id"],
        delete_result="PASS",
    )
    gates_passed, gate_failures = required_gates_passed(gates2["gates"])
    assert gates_passed, gate_failures
    policy_passed, policy_reasons = evaluate_pass_policy(
        package2,
        required_acceptance_criteria_checked=True,
        required_generator_handoff_present=True,
        required_evidence_complete=True,
        protected_artifacts_unchanged=True,
        prior_blocking_issues_answered=True,
    )
    assert policy_passed, policy_reasons

    final_state = apply_controlled_retry(
        load_project_state(root / "project.yaml"), package2
    )
    assert final_state["status"] == "ACCEPTED"
    assert final_state["current_iteration"] == 1
    _cas_evaluation_commit(
        root,
        orchestrator,
        evaluator_second_start,
        final_state,
        package2,
        gates2,
        idempotency_key="stage3-evaluation-002-pass",
    )
    accepted = load_project_state(root / "project.yaml")
    assert accepted["status"] == "ACCEPTED"
    assert accepted["next_role"] is None
    assert accepted["current_iteration"] == 1
    assert accepted["last_evaluation"] == REPORT_002
    assert accepted["last_issue_package"] == ISSUE_002
    assert accepted["evidence_manifest"] == MANIFEST_002
    assert (root / REPORT_001).is_file()
    assert (root / REPORT_002).is_file()
    assert (root / ISSUE_001).is_file()
    assert (root / ISSUE_002).is_file()
    assert (root / MANIFEST_001).is_file()
    assert (root / MANIFEST_002).is_file()
    assert compare_protected_snapshot(root, protected_before)["unchanged"]

    metrics2 = compute_iteration_metrics(loaded_issue1, package2)
    repeat2 = update_repeat_history(repeat1, package2, stalled_threshold=2)
    decision2 = write_decision_summary(
        root,
        build_decision_summary(
            accepted,
            [loaded_issue1, package2],
            [metrics1, metrics2],
            repeat2,
            [generator_response],
            recheck_records,
            reason="issue_resolved_and_evaluation_passed",
        ),
    )
    assert decision2.is_file()
    assert decision2 != decision1

    limit_probe_state = copy.deepcopy(accepted)
    limit_probe_state.update(
        {
            "status": "EVALUATING",
            "next_role": "evaluator",
            "current_iteration": 4,
            "automatic_retry_allowed": True,
        }
    )
    limit_probe = apply_controlled_retry(limit_probe_state, package1)
    assert MAXIMUM_AUTOMATIC_ITERATIONS == 5
    assert limit_probe["current_iteration"] == 5
    assert limit_probe["status"] == "WAITING_FOR_USER"
    assert limit_probe["automatic_retry_allowed"] is False

    events = store.list_events(session_id)
    event_types = {event.event_type for event in events}
    assert "PROJECT_STATE_COMMITTED" in event_types
    assert "ROLE_COMPLETED" in event_types
    assert "TOOL_CALL_COMPLETED" in event_types
    assert sum(event.event_type == "PROJECT_STATE_COMMITTED" for event in events) >= 8
    assert all("api_key" not in str(event.payload).lower() for event in events)
