"""Production Readiness Stage 2：审批双 Gate 到首次 ACCEPTED 的真实组合测试。"""

from __future__ import annotations

import copy
import hashlib
import re
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
from runtime.leases import LeaseManager
from runtime.orchestrator import Orchestrator
from runtime.project_revision import ProjectStateCAS
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
)
from scripts.evaluation_protocol import evaluate_pass_policy
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
from tests.test_evaluation_protocol import make_package


PROJECT_ID = "test_stage2_todo_app"
PRODUCT_APPROVAL = "memory/decisions/product-approval-001.md"
PRODUCT_SPEC = "memory/specifications/product_spec_v001.md"
PLAN = "memory/plans/plan-001.md"
PLAN_APPROVAL = "memory/decisions/plan-approval-001.md"
HANDOFF = "memory/handoffs/handoff-001.md"


def _write(root: Path, reference: str, content: str) -> None:
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_artifact(title: str, sections: tuple[str, ...], references: list[str]) -> str:
    source_line = "来源：" + "、".join(f"`{item}`" for item in references)
    lines = [f"# {title}", ""]
    for section in sections:
        lines.extend((f"## {section}", source_line, "Todo Web App 的最小可验证实现。", ""))
    return "\n".join(lines)


def _changed_fields(before: dict, after: dict) -> dict:
    fields = {
        key: copy.deepcopy(value)
        for key, value in after.items()
        if before.get(key) != value
    }
    # 工作流迁移协议要求显式提交完整的 Runtime 生命周期投影。
    if before.get("status") != after.get("status"):
        for field in ("status", "next_role", "active_module"):
            fields[field] = copy.deepcopy(after.get(field))
    return fields


def _seed_project(tmp_path: Path) -> tuple[Path, Path, SessionStore, Orchestrator]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane(PROJECT_ID, home=control_home)
    store = SessionStore(control_plane / "sessions.sqlite3")
    session = store.create_session(PROJECT_ID, root, idempotency_key="stage2-session")

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
    return root, control_home, store, Orchestrator(
        root, control_plane_home=control_home
    )


def _lease(orchestrator: Orchestrator, worker_id: str):
    return orchestrator.leases.acquire(
        orchestrator.store.get_session(
            load_project_state(orchestrator.project_yaml)["runtime"]["session_id"]
        ).session_id,
        worker_id,
    )


def _session_id(root: Path) -> str:
    return str(load_project_state(root / "project.yaml")["runtime"]["session_id"])


def _commit_user_transition(
    orchestrator: Orchestrator,
    root: Path,
    changed_fields: dict,
    *,
    source_status: str,
    target_status: str,
    worker_id: str,
    idempotency_key: str,
) -> dict:
    session_id = _session_id(root)
    lease = orchestrator.leases.acquire(session_id, worker_id)
    try:
        current = load_project_state(root / "project.yaml")
        return orchestrator.cas.commit_patch(
            root / "project.yaml",
            changed_fields,
            source_status=source_status,
            target_status=target_status,
            session_id=session_id,
            worker_id=worker_id,
            actor_role="planner",
            lease_version=lease.lease_version,
            lease_token=lease.lease_token or "",
            expected_revision=current["runtime"]["revision"],
            idempotency_key=idempotency_key,
        )
    finally:
        orchestrator.leases.release(
            session_id,
            worker_id,
            lease.lease_version,
            lease.lease_token or "",
        )


def _execution_context(root: Path, start: dict, role: str) -> ExecutionContext:
    state = load_project_state(root / "project.yaml")
    return ExecutionContext(
        session_id=start["session_id"],
        run_id=start["run_id"],
        worker_id=start["worker_id"],
        lease_version=start["lease_version"],
        role=role,
        project_id=state["project_id"],
        project_root=str(root),
    )


def test_stage2_product_approval_plan_generator_evaluator_accepted(
    tmp_path: Path,
) -> None:
    root, _control_home, store, orchestrator = _seed_project(tmp_path)
    session_id = _session_id(root)

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
        worker_id="product-approval-worker",
        idempotency_key="stage2-product-approval",
    )

    planner_start = orchestrator.start(worker_id="planner-stage2-worker")
    assert planner_start["selection"].target == "planner"
    planner_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, planner_start["run_id"], "planner")
    )
    assert planner_context.workflow_state == "PLANNING"
    assert any(source.reference == PROPOSAL for source in planner_context.sources)

    source_refs = [REQUIREMENTS, PROPOSAL, PRODUCT_APPROVAL, FEEDBACK, SELECTION]
    _write(root, PRODUCT_SPEC, _render_artifact("正式产品规格 v001", PRODUCT_SPEC_SECTIONS, source_refs))
    _write(
        root,
        PLAN,
        _render_artifact(
            "开发计划 plan-001",
            PLAN_SECTIONS,
            [*source_refs, PRODUCT_SPEC],
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
    orchestrator.commit_step(
        session_id,
        planner_start["run_id"],
        planner_start["lease_token"],
        {
            "source_status": "PLANNING",
            "target_status": "WAITING_FOR_PLAN_REVIEW",
            "changed_fields": _changed_fields(current, planner_target),
            "expected_revision": current["runtime"]["revision"],
            "idempotency_key": "stage2-formal-spec-and-plan",
        },
    )
    state = load_project_state(root / "project.yaml")
    assert state["status"] == "WAITING_FOR_PLAN_REVIEW"
    assert state["user_approval_status"] == "approved"
    assert state["active_product_spec"] == PRODUCT_SPEC
    assert state["active_plan"] == PLAN

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
        worker_id="plan-approval-worker",
        idempotency_key="stage2-plan-approval",
    )
    state = load_project_state(root / "project.yaml")
    assert state["status"] == "APPROVED_FOR_IMPLEMENTATION"
    assert state["approved_plan"] == PLAN
    assert state["plan_approval_record"] == PLAN_APPROVAL

    generator_gate_start = orchestrator.start(worker_id="generator-stage2-worker")
    assert generator_gate_start["selection"].target == "generator"
    orchestrator.commit_step(
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
            "idempotency_key": "stage2-enter-implementing",
        },
    )
    generator_start = orchestrator.start(worker_id="generator-stage2-worker")
    assert generator_start["selection"].target == "generator"
    generator_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, generator_start["run_id"], "generator")
    )
    assert any(source.reference == PLAN for source in generator_context.sources)
    generator_context_runtime = _execution_context(root, generator_start, "generator")
    environment = LocalCompatibilityEnvironment()
    broker = ExecutionBroker(store, orchestrator.leases)
    broker.provision(
        generator_context_runtime,
        environment,
        lease_token=generator_start["lease_token"],
    )
    assert "开发计划 plan-001" in broker.read_file(
        generator_context_runtime,
        environment,
        PLAN,
        lease_token=generator_start["lease_token"],
    )
    with pytest.raises(RuntimeValidationError, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        broker.write_file(
            generator_context_runtime,
            environment,
            "project.yaml",
            "status: ACCEPTED\n",
            lease_token=generator_start["lease_token"],
        )

    app_code = """from dataclasses import dataclass\n\n\n@dataclass\nclass TodoList:\n    items: list[str]\n\n    def add(self, title: str) -> None:\n        self.items.append(title)\n\n    def delete(self, title: str) -> None:\n        self.items.remove(title)\n\n    def list(self) -> list[str]:\n        return list(self.items)\n"""
    app_tests = """import unittest\n\nfrom todo_app import TodoList\n\n\nclass TodoListTests(unittest.TestCase):\n    def test_add_and_list(self) -> None:\n        todos = TodoList([])\n        todos.add('write test')\n        self.assertEqual(['write test'], todos.list())\n\n    def test_delete(self) -> None:\n        todos = TodoList(['remove me'])\n        todos.delete('remove me')\n        self.assertEqual([], todos.list())\n\n\nif __name__ == '__main__':\n    unittest.main()\n"""
    broker.write_file(
        generator_context_runtime,
        environment,
        "code/todo_app.py",
        app_code,
        lease_token=generator_start["lease_token"],
    )
    broker.write_file(
        generator_context_runtime,
        environment,
        "code/test_todo.py",
        app_tests,
        lease_token=generator_start["lease_token"],
    )
    execution_request = ExecutionRequest(
        logical_call_id="generator-self-test",
        argv=("python", "-m", "unittest", "discover", "-s", "code", "-p", "test_*.py"),
        execution_profile=ExecutionProfile("compatibility", "a" * 64, "b" * 64),
        timeout=30,
    )
    execution_receipt = broker.execute(
        generator_context_runtime,
        execution_request,
        environment,
        lease_token=generator_start["lease_token"],
    )
    assert execution_receipt.status == "SUCCEEDED"
    _write(
        root,
        HANDOFF,
        "\n".join(
            (
                "# Generator Handoff handoff-001",
                "",
                "实现 TodoList 的新增、列表和删除行为。",
                f"- code/todo_app.py sha256: {hashlib.sha256(app_code.encode()).hexdigest()}",
                f"- code/test_todo.py sha256: {hashlib.sha256(app_tests.encode()).hexdigest()}",
                "- 自测：python -m unittest discover -s code -p test_*.py，退出码 0。",
                "- 回滚：删除本轮新增 code 文件并恢复批准基线。",
            )
        ),
    )
    current = load_project_state(root / "project.yaml")
    generator_target = copy.deepcopy(current)
    generator_target.update(
        {
            "status": "EVALUATING",
            "next_role": "evaluator",
            "active_module": None,
            "last_generator_response": HANDOFF,
        }
    )
    orchestrator.commit_step(
        session_id,
        generator_start["run_id"],
        generator_start["lease_token"],
        {
            "source_status": "IMPLEMENTING",
            "target_status": "EVALUATING",
            "changed_fields": _changed_fields(current, generator_target),
            "expected_revision": current["runtime"]["revision"],
            "idempotency_key": "stage2-generator-handoff",
        },
    )

    evaluator_start = orchestrator.start(worker_id="evaluator-stage2-worker")
    assert evaluator_start["selection"].target == "evaluator"
    evaluator_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, evaluator_start["run_id"], "evaluator")
    )
    assert any(source.reference == PLAN for source in evaluator_context.sources)
    assert any(source.reference == HANDOFF for source in evaluator_context.sources)
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

    command = ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]
    test_metrics = {"passed": 2, "failed": 0, "skipped": 0, "failed_tests": []}
    test_record = run_verified_command(
        root,
        "evaluation-001",
        "CMD-001",
        "GATE-TESTS",
        command,
        allowed_prefixes=[["python", "-m", "unittest"]],
        cwd="code",
        test_metrics=test_metrics,
    )
    regression_record = run_verified_command(
        root,
        "evaluation-001",
        "CMD-002",
        "GATE-REGRESSION",
        command,
        allowed_prefixes=[["python", "-m", "unittest"]],
        cwd="code",
    )
    assert test_record["status"] == "PASSED"
    assert regression_record["status"] == "PASSED"

    protected_before = build_protected_snapshot(
        root,
        ["memory/plans", "memory/specifications", "memory/decisions", "config/evaluation_rules", "config/schemas", "code"],
    )
    package = make_package(None)
    package.update(
        {
            "evaluation_id": "evaluation-001",
            "project_id": PROJECT_ID,
            "current_iteration": load_project_state(root / "project.yaml")["current_iteration"],
            "report_reference": "evaluation/reports/evaluation-001.md",
        }
    )
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "created_at": capture_environment()["python_version"] and "2026-08-08T00:00:00+00:00",
        "environment": capture_environment("code"),
        "commands": [test_record, regression_record],
        "artifacts": [
            {"artifact_id": "ART-001", "type": "other", "path": HANDOFF, "linked_issue_ids": [], "linked_requirement_ids": ["REQ-001", "REQ-002", "REQ-003"]},
            {"artifact_id": "ART-002", "type": "other", "path": "code/todo_app.py", "linked_issue_ids": [], "linked_requirement_ids": ["REQ-001", "REQ-002", "REQ-003"]},
        ],
        "checks": [
            {"check_id": "CHECK-001", "gate_id": "GATE-REQUIREMENTS", "required": True, "requirement_id": "REQ-001", "acceptance_criterion_id": "AC-REQ-001", "result": "PASS", "evidence_refs": ["CMD-001", "ART-002"]},
            {"check_id": "CHECK-002", "gate_id": "GATE-REQUIREMENTS", "required": True, "requirement_id": "REQ-002", "acceptance_criterion_id": "AC-REQ-002", "result": "PASS", "evidence_refs": ["CMD-001", "ART-002"]},
            {"check_id": "CHECK-003", "gate_id": "GATE-REQUIREMENTS", "required": True, "requirement_id": "REQ-003", "acceptance_criterion_id": "AC-REQ-003", "result": "PASS", "evidence_refs": ["CMD-001", "ART-002"]},
        ],
        "gates": evaluate_gates(
            {
                "GATE-DELIVERY": {"result": "PASS", "evidence_refs": ["ART-001"]},
                "GATE-TESTS": {"result": "PASS", "evidence_refs": ["CMD-001"]},
                "GATE-REQUIREMENTS": {"result": "PASS", "evidence_refs": ["CHECK-001", "CHECK-002", "CHECK-003"]},
                "GATE-REGRESSION": {"result": "PASS", "evidence_refs": ["CMD-002"]},
                "GATE-EVIDENCE": {"result": "PASS", "evidence_refs": ["ART-001", "ART-002"]},
            },
            [
                {"id": "GATE-DELIVERY", "required": True},
                {"id": "GATE-BUILD", "required": False},
                {"id": "GATE-TESTS", "required": True},
                {"id": "GATE-REQUIREMENTS", "required": True},
                {"id": "GATE-REGRESSION", "required": True},
                {"id": "GATE-NON_FUNCTIONAL", "required": False},
                {"id": "GATE-EVIDENCE", "required": True},
            ],
        ),
    }
    passed, reasons = required_gates_passed(manifest["gates"])
    assert passed, reasons
    policy_passed, policy_reasons = evaluate_pass_policy(
        package,
        required_acceptance_criteria_checked=True,
        required_generator_handoff_present=True,
        required_evidence_complete=True,
        protected_artifacts_unchanged=True,
    )
    assert policy_passed, policy_reasons

    evaluator_lease = orchestrator.leases.get(session_id)

    def cas_state_writer(_path: str | Path, next_state: dict) -> None:
        current_state = load_project_state(root / "project.yaml")
        fields = _changed_fields(current_state, next_state)
        orchestrator.cas.commit_patch(
            root / "project.yaml",
            fields,
            source_status="EVALUATING",
            target_status="ACCEPTED",
            session_id=session_id,
            worker_id="evaluator-stage2-worker",
            actor_role="evaluator",
            lease_version=evaluator_lease.lease_version,
            lease_token=evaluator_start["lease_token"],
            expected_revision=current_state["runtime"]["revision"],
            idempotency_key="stage2-evaluation-accepted",
        )

    final_state = load_project_state(root / "project.yaml")
    final_state.update({"status": "ACCEPTED", "next_role": None, "active_module": None})
    commit_reproducible_evaluation(
        root,
        package,
        manifest,
        final_state,
        state_writer=cas_state_writer,
    )
    accepted = load_project_state(root / "project.yaml")
    assert accepted["status"] == "ACCEPTED"
    assert accepted["next_role"] is None
    assert accepted["last_evaluation"] == "evaluation/reports/evaluation-001.md"
    assert accepted["evidence_manifest"] == "evaluation/evidence/evaluation-001/manifest.yaml"
    assert compare_protected_snapshot(root, protected_before)["unchanged"]
    assert any(event.event_type == "PROJECT_STATE_COMMITTED" for event in store.list_events(session_id))
