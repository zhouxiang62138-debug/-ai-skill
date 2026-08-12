"""T17 Role Thread Isolation 的可复现 Runtime 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.event_types import EventType
from runtime.errors import LeaseError, RuntimeStorageError, RuntimeValidationError
from runtime.orchestrator import Orchestrator
from runtime.phase_runner import PhaseRunner
from runtime.role_execution import (
    ExecutionMode,
    HostCapabilityProfile,
    RoleExecutionRequest,
    WorkspaceBinding,
    WorkspaceMode,
)
from scripts.project_state import load_project_state, serialize_project_state
from tests.runtime_test_support import make_runtime_project


class FakeModel:
    """只提供模型身份；Child Host 或 fallback Broker 负责真正调用。"""

    model_id = "isolation-test-model"

    def invoke(self, request):
        return {"completed_steps": list(request.required_steps)}


class FakeChildHost:
    """模拟真实宿主适配器，返回稳定且不等于主线程的 Child Thread ID。"""

    def __init__(self, *, workspace_mode: WorkspaceMode = WorkspaceMode.SHARED) -> None:
        self.profile = HostCapabilityProfile(
            child_thread_supported=True,
            stable_thread_id=True,
            programmatic_spawn=True,
            resumable=False,
            fresh_invocation_supported=True,
            workspace_mode=workspace_mode,
            host_name="fake-codex-host",
        )
        self.created: list[str] = []
        self.invocations: list[tuple[str, object]] = []
        self.archived: list[str] = []
        self.fail_next = False
        self.crash_on_create = False

    def capabilities(self) -> HostCapabilityProfile:
        return self.profile

    def create_child_thread(self, request: RoleExecutionRequest) -> str:
        if self.crash_on_create:
            raise KeyboardInterrupt("simulated crash during child creation")
        thread_id = f"THREAD-{request.role.upper()}-{len(self.created) + 1:03d}"
        self.created.append(thread_id)
        return thread_id

    def invoke_child(self, thread_id: str, request) -> dict[str, object]:
        self.invocations.append((thread_id, request))
        if self.fail_next:
            self.fail_next = False
            return {"completed_steps": []}
        return {"completed_steps": list(request.required_steps)}

    def terminate_child(self, thread_id: str, *, reason: str) -> None:
        self.archived.append(thread_id)


def _orchestrator(tmp_path: Path, *, host=None, status: str = "PLANNING"):
    root, session_id = make_runtime_project(str(tmp_path), status=status)
    home = Path((root / ".test-control-plane-home").read_text())
    return root, session_id, Orchestrator(
        root, control_plane_home=home, role_execution_host=host
    )


def _planner_runner(orchestrator: Orchestrator, model=None) -> tuple[dict, PhaseRunner]:
    started = orchestrator.start(worker_id=f"worker-{id(orchestrator)}")
    model = model or FakeModel()
    verifiers = {
        step: lambda request, response: True
        for step in ("source_chain", "proposal_or_plan", "handoff")
    }
    return started, PhaseRunner(
        orchestrator,
        model,
        gate_verifiers=verifiers,
        test_only_verifiers=True,
    )


def _build_execution(orchestrator: Orchestrator, started: dict) -> dict:
    context = ContextBuilder(orchestrator.store).build(
        ContextBuildRequest(
            str(started["session_id"]),
            str(started["run_id"]),
            "planner",
        )
    )
    session = orchestrator.store.get_session(str(started["session_id"]))
    broker = orchestrator.role_execution_broker
    return broker.start_role_execution(
        RoleExecutionRequest(
            session_id=session.session_id,
            run_id=str(started["run_id"]),
            role="planner",
            project_id=session.project_id,
            project_root=session.project_root,
            source_revision=context.project_revision,
            context_manifest_id=context.context_id,
            workspace_binding=WorkspaceBinding(
                broker.policy.workspace_mode,
                session.project_root,
                context.project_revision,
            ),
            preferred_mode=broker.policy.preferred_for("planner"),
            fallback_mode=broker.policy.fallback_mode,
        )
    )


def test_fresh_planner_role_runs_get_distinct_child_threads(tmp_path: Path) -> None:
    host = FakeChildHost()
    _, _, orchestrator = _orchestrator(tmp_path, host=host)
    first, runner = _planner_runner(orchestrator)
    first_result = runner.run(
        first["session_id"], first["run_id"], first["lease_token"], idempotency_key="p-1"
    )
    second, runner = _planner_runner(orchestrator)
    second_result = runner.run(
        second["session_id"], second["run_id"], second["lease_token"], idempotency_key="p-2"
    )
    assert first_result.status == second_result.status == "COMPLETED"
    assert host.created == ["THREAD-PLANNER-001", "THREAD-PLANNER-002"]
    assert first_result.role_execution_id != second_result.role_execution_id


def test_host_child_thread_execution_is_not_roleplay(tmp_path: Path) -> None:
    host = FakeChildHost()
    _, _, orchestrator = _orchestrator(tmp_path, host=host)
    started, runner = _planner_runner(orchestrator)
    result = runner.run(
        started["session_id"], started["run_id"], started["lease_token"], idempotency_key="child-1"
    )
    execution = orchestrator.store.get_role_execution(
        started["session_id"], result.role_execution_id
    )
    assert result.execution_mode == ExecutionMode.CHILD_THREAD.value
    assert execution["host_thread_id"] == "THREAD-PLANNER-001"
    assert host.invocations[0][0] == execution["host_thread_id"]
    assert host.archived == ["THREAD-PLANNER-001"]


def test_unsupported_child_thread_falls_back_to_fresh_invocation(tmp_path: Path) -> None:
    _, _, orchestrator = _orchestrator(tmp_path)
    started, runner = _planner_runner(orchestrator)
    result = runner.run(
        started["session_id"], started["run_id"], started["lease_token"], idempotency_key="fallback-1"
    )
    execution = orchestrator.store.get_role_execution(
        started["session_id"], result.role_execution_id
    )
    assert result.status == "COMPLETED"
    assert execution["execution_mode"] == ExecutionMode.FRESH_INVOCATION.value
    assert execution["host_thread_id"] is None
    assert execution["fallback_reason"] == "HOST_CHILD_THREAD_UNAVAILABLE"
    assert any(
        event.event_type == EventType.ROLE_EXECUTION_FALLBACK
        for event in orchestrator.store.list_events(started["session_id"])
    )


def test_role_run_cannot_bind_another_run_invocation(tmp_path: Path) -> None:
    _, _, orchestrator = _orchestrator(tmp_path)
    first, _ = _planner_runner(orchestrator)
    execution = _build_execution(orchestrator, first)
    first_context = orchestrator.store.get_context_manifest(
        first["session_id"], execution["context_manifest_id"]
    )
    second_run = orchestrator.store.create_role_run(
        first["session_id"], "worker-2", "planner", source_revision=0
    )
    second_context = ContextBuilder(orchestrator.store).build(
        ContextBuildRequest(first["session_id"], second_run, "planner")
    )
    invocation = orchestrator.store.create_model_invocation(
        first["session_id"],
        second_run,
        "planner",
        second_context["context_id"] if isinstance(second_context, dict) else second_context.context_id,
        idempotency_key="wrong-run-invocation",
    )
    with pytest.raises(RuntimeValidationError, match="INVOCATION_MISMATCH"):
        orchestrator.role_execution_broker.bind_invocation(
            first["session_id"], execution["role_execution_id"], invocation["invocation_id"]
        )
    assert first_context["run_id"] == first["run_id"]


def test_phase_attestation_contains_role_execution_binding(tmp_path: Path) -> None:
    _, _, orchestrator = _orchestrator(tmp_path)
    started, runner = _planner_runner(orchestrator)
    result = runner.run(
        started["session_id"], started["run_id"], started["lease_token"], idempotency_key="attest-1"
    )
    attestation = orchestrator.store.get_phase_attestation(
        started["session_id"], result.attestation_id
    )
    assert attestation["role_execution_id"] == result.role_execution_id
    assert attestation["execution_mode"] == ExecutionMode.FRESH_INVOCATION.value
    assert attestation["host_thread_id"] is None


def test_evaluator_context_stays_independent_of_generator_private_response(tmp_path: Path) -> None:
    root, session_id, orchestrator = _orchestrator(tmp_path, status="PLANNING")
    state = load_project_state(root / "project.yaml")
    state["status"] = "EVALUATING"
    state["next_role"] = "evaluator"
    state["active_module"] = None
    (root / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
    started = orchestrator.start(worker_id="evaluator-worker")
    private = root / "memory/handoffs/responses/private.md"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("GENERATOR_THREAD_PRIVATE_81723", encoding="utf-8")
    context = ContextBuilder(orchestrator.store).build(
        ContextBuildRequest(session_id, started["run_id"], "evaluator")
    )
    assert context.context_type == "EVALUATOR_INDEPENDENT"
    assert all("GENERATOR_THREAD_PRIVATE_81723" not in str(item.to_dict()) for item in context.sources)


def test_wait_state_has_no_role_execution_or_lease(tmp_path: Path) -> None:
    root, session_id, orchestrator = _orchestrator(tmp_path)
    state = load_project_state(root / "project.yaml")
    state["status"] = "WAITING_FOR_PRODUCT_REVIEW"
    state["next_role"] = None
    state["active_module"] = None
    (root / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
    started = orchestrator.start(worker_id="wait-worker")
    assert started["run_id"] is None
    assert orchestrator.store.active_role_executions(session_id) == []
    assert orchestrator.leases.detect_expired() == []


def test_rework_creates_new_execution_without_resetting_role_history(tmp_path: Path) -> None:
    host = FakeChildHost()
    _, _, orchestrator = _orchestrator(tmp_path, host=host)
    host.fail_next = True
    first, runner = _planner_runner(orchestrator)
    failed = runner.run(
        first["session_id"], first["run_id"], first["lease_token"], idempotency_key="rework-1"
    )
    second, runner = _planner_runner(orchestrator)
    passed = runner.run(
        second["session_id"], second["run_id"], second["lease_token"], idempotency_key="rework-2"
    )
    executions = orchestrator.store.list_role_executions(first["session_id"])
    assert failed.status == "FAILED"
    assert passed.status == "COMPLETED"
    assert len(executions) == 2
    assert executions[0]["role_execution_id"] != executions[1]["role_execution_id"]
    assert executions[0]["host_thread_id"] != executions[1]["host_thread_id"]


def test_crash_recovery_preserves_role_execution_and_marks_it_unknown(tmp_path: Path) -> None:
    class CrashModel(FakeModel):
        def invoke(self, request):
            raise KeyboardInterrupt("simulated host crash")

    root, session_id, orchestrator = _orchestrator(tmp_path)
    started, runner = _planner_runner(orchestrator, model=CrashModel())
    with pytest.raises(KeyboardInterrupt):
        runner.run(
            session_id, started["run_id"], started["lease_token"], idempotency_key="crash-1"
        )
    orchestrator.leases.revoke_for_lifecycle(session_id, reason="test-crash")
    recovered = orchestrator.recover_session(session_id, worker_id="recovery-worker")
    assert len(recovered["interrupted_role_executions"]) == 1
    execution = recovered["interrupted_role_executions"][0]
    assert execution["status"] == "UNKNOWN_AFTER_CRASH"
    assert orchestrator.store.get_role_run(session_id, started["run_id"])["status"] == "STARTED"
    assert root.is_dir()


def test_worktree_host_downgrades_when_authority_cannot_be_safely_synchronized(tmp_path: Path) -> None:
    host = FakeChildHost(workspace_mode=WorkspaceMode.WORKTREE)
    _, _, orchestrator = _orchestrator(tmp_path, host=host)
    started, runner = _planner_runner(orchestrator)
    result = runner.run(
        started["session_id"], started["run_id"], started["lease_token"], idempotency_key="worktree-1"
    )
    execution = orchestrator.store.get_role_execution(
        started["session_id"], result.role_execution_id
    )
    assert result.status == "COMPLETED"
    assert execution["execution_mode"] == ExecutionMode.FRESH_INVOCATION.value
    assert execution["fallback_reason"] == "HOST_WORKTREE_STATE_DIVERGENCE_UNSAFE"
    assert host.created == []


def test_multiple_role_executions_share_session_control_plane(tmp_path: Path) -> None:
    _, session_id, orchestrator = _orchestrator(tmp_path)
    first, _ = _planner_runner(orchestrator)
    first_execution = _build_execution(orchestrator, first)
    orchestrator.role_execution_broker.fail(
        session_id, first_execution["role_execution_id"], reason="test-finished"
    )
    orchestrator.leases.revoke_for_lifecycle(session_id, reason="test-finished")
    second, _ = _planner_runner(orchestrator)
    second_execution = _build_execution(orchestrator, second)
    assert first_execution["role_execution_id"] != second_execution["role_execution_id"]
    assert orchestrator.store.get_session(session_id).session_id == session_id
    assert orchestrator.store.path.parent.name.startswith("runtime-")


def test_wrong_execution_attestation_is_denied_before_cas(tmp_path: Path) -> None:
    _, _, orchestrator = _orchestrator(tmp_path)
    started, _ = _planner_runner(orchestrator)
    execution = _build_execution(orchestrator, started)
    with pytest.raises(RuntimeStorageError, match="ATTESTATION"):
        orchestrator.commit_step(
            started["session_id"],
            started["run_id"],
            started["lease_token"],
            {
                "source_status": "PLANNING",
                "target_status": "PLANNING",
                "changed_fields": {},
                "expected_revision": 0,
                "idempotency_key": "wrong-execution-commit",
                "attestation_id": "not-a-real-attestation",
            },
        )
    assert execution["status"] == "STARTED"


def test_pause_cancels_active_role_execution_and_releases_lease(tmp_path: Path) -> None:
    _, session_id, orchestrator = _orchestrator(tmp_path)
    started, _ = _planner_runner(orchestrator)
    execution = _build_execution(orchestrator, started)
    orchestrator.pause(session_id)
    saved = orchestrator.store.get_role_execution(session_id, execution["role_execution_id"])
    assert saved["status"] == "CANCELLED"
    assert orchestrator.store.active_role_executions(session_id) == []
    with pytest.raises(LeaseError):
        orchestrator.leases.get(session_id)


def test_capability_downgrade_does_not_fake_host_thread_id(tmp_path: Path) -> None:
    _, session_id, orchestrator = _orchestrator(tmp_path)
    started, _ = _planner_runner(orchestrator)
    execution = _build_execution(orchestrator, started)
    assert execution["execution_mode"] == "FRESH_INVOCATION"
    assert execution["host_thread_id"] is None
    assert orchestrator.role_execution_broker.capabilities().real_child_thread_ready is False
    events = orchestrator.store.list_events(session_id)
    fallback = [item for item in events if item.event_type == EventType.ROLE_EXECUTION_FALLBACK]
    assert fallback and fallback[-1].payload["fallback_reason"] == "HOST_CHILD_THREAD_UNAVAILABLE"


def test_crash_between_thread_request_and_host_creation_is_durable(tmp_path: Path) -> None:
    host = FakeChildHost()
    host.crash_on_create = True
    _, session_id, orchestrator = _orchestrator(tmp_path, host=host)
    started, _ = _planner_runner(orchestrator)
    context = ContextBuilder(orchestrator.store).build(
        ContextBuildRequest(session_id, started["run_id"], "planner")
    )
    session = orchestrator.store.get_session(session_id)
    request = RoleExecutionRequest(
        session_id=session_id,
        run_id=started["run_id"],
        role="planner",
        project_id=session.project_id,
        project_root=session.project_root,
        source_revision=context.project_revision,
        context_manifest_id=context.context_id,
        workspace_binding=WorkspaceBinding(
            WorkspaceMode.SHARED, session.project_root, context.project_revision
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        orchestrator.role_execution_broker.start_role_execution(request)
    pending = orchestrator.store.active_role_executions(session_id)
    assert len(pending) == 1 and pending[0]["status"] == "REQUESTED"
    orchestrator.leases.revoke_for_lifecycle(session_id, reason="test-crash")
    recovered = orchestrator.recover_session(session_id, worker_id="recovery-worker")
    assert recovered["interrupted_role_executions"][0]["status"] == "UNKNOWN_AFTER_CRASH"


def test_inspect_exposes_execution_metadata_and_capabilities(tmp_path: Path) -> None:
    host = FakeChildHost()
    _, session_id, orchestrator = _orchestrator(tmp_path, host=host)
    started, runner = _planner_runner(orchestrator)
    runner.run(
        session_id, started["run_id"], started["lease_token"], idempotency_key="inspect-1"
    )
    inspected = orchestrator.inspect(session_id)
    assert inspected["role_execution_capabilities"]["child_thread"]["supported"] is True
    assert len(inspected["role_executions"]) == 1
    assert inspected["role_executions"][0]["execution_mode"] == "CHILD_THREAD"
