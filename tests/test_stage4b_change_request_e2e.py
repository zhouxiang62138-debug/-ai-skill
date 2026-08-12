from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from change_request import (
    begin_change_implementation,
    create_change_release,
    create_change_request,
    create_impact_analysis,
    decide_change_request,
    record_change_evaluation,
    record_generator_change_handoff,
)
from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.control_plane import initialize_control_plane
from runtime.event_types import EventType
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
    LocalCompatibilityEnvironment,
)
from runtime.execution.path_policy import PathAccessDenied
from runtime.orchestrator import Orchestrator
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from tests.test_change_request import accepted_state


PROJECT_ID = "test_stage4b_todo_app"
CODE_HASH = "1" * 64
ENV_HASH = "2" * 64


def _write(root: Path, reference: str, content: str) -> None:
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_project(tmp_path: Path) -> tuple[Path, Path, SessionStore, Orchestrator]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane(PROJECT_ID, home=control_home)
    store = SessionStore(control_plane / "sessions.sqlite3")
    session = store.create_session(PROJECT_ID, root, idempotency_key="stage4b-session")

    state = accepted_state()
    state.update(
        {
            "project_id": PROJECT_ID,
            "active_requirements": "memory/requirements/requirements_v001.yaml",
            "active_proposal": "memory/proposals/product_proposal_v001.md",
            "approved_proposal": "memory/proposals/product_proposal_v001.md",
            "product_approval_record": "memory/decisions/product-approval-001.md",
            "active_product_spec": "memory/specifications/product_spec_v001.md",
            "active_plan": "memory/plans/plan-001.md",
            "approved_plan": "memory/plans/plan-001.md",
            "product_spec_version": 1,
            "design_exploration_required": False,
            "design_review_status": "skipped_by_user",
            "design_skip_record": "memory/decisions/design-skip-001.md",
            "last_evaluation": "evaluation/reports/evaluation-001.md",
            "current_release": "releases/release-1.0.0.yaml",
            "evaluation_profile": "default",
        }
    )
    state = preview_runtime_migration(
        state, project_root=root, session_id=session.session_id
    )
    _write(root, state["active_requirements"], "REQ-001: add, list and delete tasks\n")
    _write(root, state["active_proposal"], "# Original Product Proposal\nTodo Web App\n")
    _write(root, state["product_approval_record"], "# Product Approval\napproved\n")
    _write(root, state["active_product_spec"], "# Original Product Spec\nTodo Web App\n")
    _write(root, state["design_skip_record"], "# Design Skip\nuser approved simple UI\n")
    _write(
        root,
        state["active_plan"],
        "# Original Approved Plan\nadd task; list task; delete task\n",
    )
    _write(root, state["last_evaluation"], "# Original Evaluation\nPASS\n")
    _write(
        root,
        state["current_release"],
        "schema_version: '1.0'\nrelease_version: 1.0.0\nstatus: ACCEPTED\n",
    )
    _write(
        root,
        "code/app.py",
        "class TodoApp:\n"
        "    def __init__(self):\n"
        "        self.tasks = []\n"
        "    def add_task(self, title):\n"
        "        self.tasks.append({'title': title})\n"
        "    def list_tasks(self):\n"
        "        return list(self.tasks)\n"
        "    def delete_task(self, title):\n"
        "        self.tasks = [task for task in self.tasks if task['title'] != title]\n",
    )
    _write(
        root,
        "tests/test_todo.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parents[1] / 'code'))\n"
        "from app import TodoApp\n\n"
        "def test_original_add_list_delete():\n"
        "    app = TodoApp()\n"
        "    app.add_task('write tests')\n"
        "    assert app.list_tasks() == [{'title': 'write tests'}]\n"
        "    app.delete_task('write tests')\n"
        "    assert app.list_tasks() == []\n",
    )
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    return root, control_home, store, Orchestrator(
        root, control_plane_home=control_home
    )


def _context(start: dict, role: str, root: Path) -> tuple[ExecutionContext, str]:
    context = ExecutionContext(
        session_id=str(start["session_id"]),
        run_id=str(start["run_id"]),
        worker_id=str(start["worker_id"]),
        lease_version=int(start["lease_version"]),
        role=role,
        project_id=PROJECT_ID,
        project_root=str(root),
    )
    return context, str(start["lease_token"])


def _run_request(logical_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        logical_call_id=logical_id,
        argv=(sys.executable, "-m", "pytest", "-q"),
        cwd=".",
        timeout=60,
        execution_profile=ExecutionProfile("compatibility", CODE_HASH, ENV_HASH),
    )


def _path_denials(store: SessionStore, session_id: str):
    return [
        event
        for event in store.list_events(session_id)
        if event.event_type == EventType.PATH_ACCESS_DENIED
    ]


def test_stage4b_change_request_runs_to_accepted_with_real_generator_and_evaluator(
    tmp_path: Path,
) -> None:
    root, control_home, store, runtime = _seed_project(tmp_path)

    created = create_change_request(
        root,
        raw_feedback="Todo 任务增加完成/未完成状态，并允许点击切换",
        requested_changes=["增加任务完成/未完成状态与切换行为"],
        runtime=runtime,
        control_plane_home=control_home,
    )
    change_request_id = str(created["change_request_id"])
    session_id = str(load_project_state(root / "project.yaml")["runtime"]["session_id"])

    planner_start = runtime.start(worker_id="stage4b-planner")
    planner_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, str(planner_start["run_id"]), "planner")
    )
    planner_refs = {source.reference for source in planner_context.sources}
    assert "change_requests/CR-0001.yaml" in planner_refs
    assert "memory/plans/plan-001.md" in planner_refs
    assert "evaluation/reports/evaluation-001.md" in planner_refs
    assert planner_context.project_id == PROJECT_ID
    assert all(".runtime" not in source.reference for source in planner_context.sources)

    item_id = f"{change_request_id}-01"
    impact = create_impact_analysis(
        root,
        change_request_id,
        item_impacts=[
            {
                "change_item_id": item_id,
                "classification": "new_feature",
                "requirement_action": "ADD",
                "affected_requirements": ["REQ-001"],
                "new_requirement": {
                    "requirement_id": "REQ-CHANGE-01",
                    "description": "任务具有完成/未完成状态并可切换",
                    "acceptance_criteria": [
                        {
                            "acceptance_criterion_id": "AC-CHANGE-01-01",
                            "description": "切换后状态可被读取",
                        }
                    ],
                },
                "affected_acceptance_criteria": ["AC-001-01"],
                "affected_components": ["code/app.py", "tests/test_todo.py"],
                "data_model_impact": False,
                "api_impact": False,
                "compatibility_impact": False,
                "new_dependency_required": False,
                "regression_areas": ["add", "list", "delete"],
                "implementation_recommendation": "保留原有任务操作并增加 toggle_task",
                "requested_change": "增加任务完成/未完成状态与切换行为",
                "acceptance_impact": "新增 AC-CHANGE-01-01",
                "regression_risk": "原有增删列功能回归",
            }
        ],
        runtime=runtime,
        control_plane_home=control_home,
        run_context=planner_start,
    )
    assert impact["project_status"] == "WAITING_FOR_CHANGE_APPROVAL"

    approval = decide_change_request(
        root,
        change_request_id,
        item_decisions={item_id: "APPROVED"},
        source_text="我批准这个修改范围",
        runtime=runtime,
        control_plane_home=control_home,
    )
    assert approval["decision"] == "APPROVED"
    assert load_project_state(root / "project.yaml")["status"] == "WAITING_FOR_CHANGE_APPROVAL"
    original_plan = (root / "memory/plans/plan-001.md").read_bytes()
    assert original_plan
    assert Path(approval["plan"]).name == "plan-002.md"

    implementation = begin_change_implementation(
        root,
        change_request_id,
        runtime=runtime,
        control_plane_home=control_home,
    )
    assert implementation["project_status"] == "IMPLEMENTING"
    generator_start = runtime.start(worker_id="stage4b-generator")
    generator_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, str(generator_start["run_id"]), "generator")
    )
    generator_resume = ContextBuilder(store).build_resume(
        ContextBuildRequest(session_id, str(generator_start["run_id"]), "generator")
    )
    generator_refs = {source.reference for source in generator_context.sources}
    assert "memory/plans/plan-002.md" in generator_refs
    assert "change_requests/CR-0001.yaml" in generator_refs
    assert "change_requests/CR-0001/baseline/manifest.yaml" in generator_refs
    assert generator_resume.resume_mode == "INCREMENTAL"
    assert generator_context.context_hash == generator_resume.context.context_hash

    generator_execution, generator_token = _context(generator_start, "generator", root)
    environment = LocalCompatibilityEnvironment()
    broker = ExecutionBroker(store, runtime.leases)
    broker.provision(generator_execution, environment, lease_token=generator_token)
    with pytest.raises(PathAccessDenied, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        broker.write_file(
            generator_execution,
            environment,
            "project.yaml",
            "status: ACCEPTED\n",
            lease_token=generator_token,
        )
    assert len(_path_denials(store, session_id)) == 1

    broker.write_file(
        generator_execution,
        environment,
        "code/app.py",
        "class TodoApp:\n"
        "    def __init__(self):\n"
        "        self.tasks = []\n"
        "    def add_task(self, title):\n"
        "        self.tasks.append({'title': title})\n"
        "    def list_tasks(self):\n"
        "        return [dict(task) for task in self.tasks]\n"
        "    def delete_task(self, title):\n"
        "        self.tasks = [task for task in self.tasks if task['title'] != title]\n"
        "    def toggle_task(self, title):\n"
        "        for task in self.tasks:\n"
        "            if task['title'] == title:\n"
        "                task.setdefault('completed', False)\n"
        "                task['completed'] = not task['completed']\n"
        "                return dict(task)\n"
        "        raise KeyError(title)\n",
        lease_token=generator_token,
    )
    broker.write_file(
        generator_execution,
        environment,
        "tests/test_todo_complete.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parents[1] / 'code'))\n"
        "from app import TodoApp\n\n"
        "def test_complete_incomplete_toggle():\n"
        "    app = TodoApp()\n"
        "    app.add_task('ship')\n"
        "    assert app.toggle_task('ship')['completed'] is True\n"
        "    assert app.list_tasks()[0]['completed'] is True\n"
        "    assert app.toggle_task('ship')['completed'] is False\n",
        lease_token=generator_token,
    )
    generator_receipt = broker.execute(
        generator_execution,
        _run_request("stage4b-generator-regression"),
        environment,
        lease_token=generator_token,
    )
    assert generator_receipt.status == "SUCCEEDED", (
        f"exit={generator_receipt.result.exit_code}; "
        f"stdout={generator_receipt.result.stdout}; "
        f"stderr={generator_receipt.result.stderr}"
    )
    handoff = record_generator_change_handoff(
        root,
        change_request_id,
        implemented_items=[
            {
                "change_item_id": item_id,
                "implementation": "保留增删列并增加完成状态 toggle",
            }
        ],
        changed_files=["code/app.py", "tests/test_todo_complete.py"],
        verification_results=[
            {"command": "python -m pytest -q", "status": "PASS"}
        ],
        rollback="依据 Change Request baseline manifest 恢复 code 与测试文件",
        runtime=runtime,
        control_plane_home=control_home,
        run_context=generator_start,
    )
    assert handoff["project_status"] == "EVALUATING"

    evaluator_start = runtime.start(worker_id="stage4b-evaluator")
    evaluator_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, str(evaluator_start["run_id"]), "evaluator")
    )
    evaluator_refs = {source.reference for source in evaluator_context.sources}
    assert "memory/plans/plan-002.md" in evaluator_refs
    assert "change_requests/CR-0001/handoffs/handoff-001.yaml" in evaluator_refs
    assert "evaluation/reports/evaluation-001.md" in evaluator_refs

    evaluator_execution, evaluator_token = _context(evaluator_start, "evaluator", root)
    evaluator_environment = LocalCompatibilityEnvironment()
    evaluator_broker = ExecutionBroker(store, runtime.leases)
    evaluator_broker.provision(
        evaluator_execution, evaluator_environment, lease_token=evaluator_token
    )
    with pytest.raises(PathAccessDenied, match="EXECUTION_PATH_PROHIBITED"):
        evaluator_broker.write_file(
            evaluator_execution,
            evaluator_environment,
            "code/app.py",
            "forbidden = True\n",
            lease_token=evaluator_token,
        )
    assert len(_path_denials(store, session_id)) == 2
    evaluator_receipt = evaluator_broker.execute(
        evaluator_execution,
        _run_request("stage4b-evaluator-regression"),
        evaluator_environment,
        lease_token=evaluator_token,
    )
    assert evaluator_receipt.status == "SUCCEEDED"
    evaluator_broker.write_file(
        evaluator_execution,
        evaluator_environment,
        "evaluation/evidence/change-regression-001.txt",
        "original add/list/delete: PASS\nnew toggle complete/incomplete: PASS\n",
        lease_token=evaluator_token,
    )
    evaluation = record_change_evaluation(
        root,
        change_request_id,
        change_item_results=[
            {
                "change_item_id": item_id,
                "requirement_id": "REQ-CHANGE-01",
                "acceptance_criterion_ids": ["AC-CHANGE-01-01"],
                "evidence_ids": ["evaluation/evidence/change-regression-001.txt"],
                "status": "PASS",
            }
        ],
        regression_results=[
            {
                "regression_id": "REG-ORIGINAL-001",
                "evidence_ids": ["evaluation/evidence/change-regression-001.txt"],
                "status": "PASS",
            }
        ],
        evidence=["evaluation/evidence/change-regression-001.txt"],
        runtime=runtime,
        control_plane_home=control_home,
        run_context=evaluator_start,
    )
    assert evaluation["result"] == "PASS"
    assert evaluation["project_status"] == "RELEASE_READY"

    release = create_change_release(
        root,
        change_request_id,
        runtime=runtime,
        control_plane_home=control_home,
    )
    final_state = load_project_state(root / "project.yaml")
    assert release["project_status"] == "ACCEPTED"
    assert final_state["status"] == "ACCEPTED"
    assert final_state["active_change_request"] is None
    assert (root / "memory/plans/plan-001.md").read_bytes() == original_plan
    assert (root / "evaluation/reports/evaluation-001.md").is_file()
    assert Path(evaluation["evaluation"]).is_file()

    durable = SessionStore(store.path).list_events(session_id)
    stages = {
        event.payload.get("stage")
        for event in durable
        if event.event_type == EventType.CHANGE_REQUEST_STAGE
    }
    assert {
        "IMPACT_ANALYSIS_COMPLETED",
        "CHANGE_SCOPE_APPROVED",
        "CHANGE_IMPLEMENTATION_STARTED",
        "GENERATOR_HANDOFF",
        "EVALUATION_PASS",
        "RELEASE_ACCEPTED",
    } <= stages
    serialized = " ".join(str(event.payload) for event in durable)
    assert "Todo 任务增加完成/未完成状态" not in serialized
    assert len(_path_denials(store, session_id)) == 2
