"""PhaseRunner 的真实 Runtime 闭环测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.event_types import EventType
from runtime.errors import RuntimeValidationError
from runtime.phase_runner import PhaseRunner
from runtime.orchestrator import Orchestrator
from runtime.verifiers import RuntimeVerifierRegistry
from scripts.project_state import load_project_state, serialize_project_state
from tests.runtime_test_support import make_runtime_project


class FakeModel:
    def __init__(self, *, omit: str | None = None, next_role: bool = False) -> None:
        self.requests = []
        self.omit = omit
        self.next_role = next_role

    def invoke(self, request):
        self.requests.append(request)
        steps = [item for item in request.required_steps if item != self.omit]
        result = {"completed_steps": steps}
        if self.next_role:
            result["next_role"] = "evaluator"
        return result


class CrashOnceModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def invoke(self, request):
        if not self.crashed:
            self.crashed = True
            raise KeyboardInterrupt("simulated worker crash")
        return super().invoke(request)


def _runner(tmp_path: Path, model: FakeModel):
    root, session_id = make_runtime_project(str(tmp_path))
    home = Path((root / ".test-control-plane-home").read_text())
    orchestrator = Orchestrator(root, control_plane_home=home)
    started = orchestrator.start()
    test_verifiers = {
        step: lambda request, response: True
        for step in ("source_chain", "proposal_or_plan", "handoff")
    }
    return root, session_id, orchestrator, started, PhaseRunner(
        orchestrator,
        model,
        gate_verifiers=test_verifiers,
        test_only_verifiers=True,
    )


def test_phase_runner_records_invocation_and_is_idempotent(tmp_path: Path) -> None:
    model = FakeModel()
    _, session_id, orchestrator, started, runner = _runner(tmp_path, model)
    first = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="planner-phase-1",
    )
    second = runner.run(
        session_id,
        started["run_id"],
        "unused-after-completion",
        idempotency_key="planner-phase-1",
    )
    assert first.status == "COMPLETED"
    assert second.replayed is True
    assert len(model.requests) == 1
    events = orchestrator.store.list_events(session_id)
    assert EventType.MODEL_INVOCATION_STARTED in [item.event_type for item in events]
    assert EventType.MODEL_INVOCATION_COMPLETED in [item.event_type for item in events]
    assert first.attestation_id is not None
    attestation = orchestrator.store.get_phase_attestation(session_id, first.attestation_id)
    assert attestation["run_id"] == started["run_id"]
    assert attestation["context_id"] == first.context_id


def test_phase_runner_rejects_missing_required_step_without_completion(tmp_path: Path) -> None:
    model = FakeModel(omit="handoff")
    _, session_id, orchestrator, started, runner = _runner(tmp_path, model)
    result = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="planner-phase-fail",
    )
    assert result.status == "FAILED"
    assert "PHASE_REQUIRED_STEP_MISSING" in (result.failure_reason or "")
    assert orchestrator.store.get_role_run(session_id, started["run_id"])["status"] == "FAILED"
    assert orchestrator.leases.detect_expired() == []


def test_phase_runner_rejects_caller_that_shrinks_runtime_required_steps(tmp_path: Path) -> None:
    model = FakeModel()
    _, session_id, orchestrator, started, runner = _runner(tmp_path, model)
    result = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        required_steps=("source_chain",),
        idempotency_key="planner-shrunk-steps",
    )
    assert result.status == "FAILED"
    assert "PHASE_REQUIRED_STEPS_SHRUNK" in (result.failure_reason or "")
    assert orchestrator.store.get_role_run(session_id, started["run_id"])["status"] == "FAILED"


def test_direct_commit_step_without_runtime_attestation_is_rejected(tmp_path: Path) -> None:
    root, session_id = make_runtime_project(str(tmp_path))
    home = Path((root / ".test-control-plane-home").read_text())
    orchestrator = Orchestrator(root, control_plane_home=home)
    started = orchestrator.start()
    state = load_project_state(root / "project.yaml")
    with pytest.raises(RuntimeValidationError, match="ATTESTATION"):
        orchestrator.commit_step(
            session_id,
            started["run_id"],
            started["lease_token"] or "",
            {
                "source_status": state["status"],
                "target_status": state["status"],
                "changed_fields": {},
                "expected_revision": 0,
                "idempotency_key": "direct-without-attestation",
            },
        )


def test_phase_runner_rejects_model_lifecycle_decision(tmp_path: Path) -> None:
    model = FakeModel(next_role=True)
    _, session_id, orchestrator, started, runner = _runner(tmp_path, model)
    result = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="planner-next-role-forbidden",
    )
    assert result.status == "FAILED"
    assert "MODEL_OUTPUT_NEXT_ROLE_FORBIDDEN" in (result.failure_reason or "")
    assert orchestrator.store.get_role_run(session_id, started["run_id"])["status"] == "FAILED"


def test_phase_runner_rejects_model_supplied_cas_commit(tmp_path: Path) -> None:
    class CasModel(FakeModel):
        def invoke(self, request):
            result = super().invoke(request)
            result["cas_commit"] = {"expected_revision": 0}
            return result

    _, session_id, orchestrator, started, runner = _runner(tmp_path, CasModel())
    result = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="planner-cas-intent-only",
    )
    assert result.status == "FAILED"
    assert "CAS_COMMIT_FORBIDDEN" in (result.failure_reason or "")


def test_runtime_verifier_rejects_claimed_tests_without_execution_evidence(tmp_path: Path) -> None:
    result = RuntimeVerifierRegistry(tmp_path).verify(
        "tests",
        None,
        {"completed_steps": ["tests"]},
    )
    assert result["passed"] is False
    assert result["details"] == "EXECUTION_EVIDENCE_MISSING"


def test_orchestrator_formal_entry_runs_policy_context_verifier_and_attestation(tmp_path: Path) -> None:
    root, _ = make_runtime_project(str(tmp_path))
    state = load_project_state(root / "project.yaml")
    state["active_proposal"] = "memory/proposals/product_proposal_v001.md"
    proposal = root / state["active_proposal"]
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("# test proposal\n", encoding="utf-8")
    (root / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
    home = Path((root / ".test-control-plane-home").read_text())
    orchestrator = Orchestrator(root, control_plane_home=home)

    class FormalModel(FakeModel):
        model_id = "formal-test-model"

        def invoke(self, request):
            return {
                "completed_steps": list(request.required_steps),
                "handoff_references": ["memory/proposals/product_proposal_v001.md"],
                "evidence_references": ["runtime:formal-test"],
            }

    result = orchestrator.execute_role(FormalModel(), worker_id="formal-worker")
    assert result.status == "COMPLETED"
    assert result.attestation_id is not None


def test_evaluator_phase_requires_runtime_gate_verifiers(tmp_path: Path) -> None:
    root, session_id = make_runtime_project(str(tmp_path))
    state = load_project_state(root / "project.yaml")
    state["status"] = "EVALUATING"
    state["next_role"] = "evaluator"
    (root / "project.yaml").write_text(
        serialize_project_state(state),
        encoding="utf-8",
    )
    home = Path((root / ".test-control-plane-home").read_text())
    orchestrator = Orchestrator(root, control_plane_home=home)
    started = orchestrator.start()
    model = FakeModel()
    result = PhaseRunner(orchestrator, model).run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="evaluator-gates-required",
    )
    assert result.status == "FAILED"
    assert "EVALUATOR_GATE_VERIFIER_MISSING" in (result.failure_reason or "")


def test_phase_runner_crash_recovery_marks_invocation_and_allows_new_retry(tmp_path: Path) -> None:
    model = CrashOnceModel()
    _, session_id, orchestrator, started, runner = _runner(tmp_path, model)
    try:
        runner.run(
            session_id,
            started["run_id"],
            started["lease_token"] or "",
            idempotency_key="planner-crash",
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("测试模型必须模拟进程崩溃")
    assert len(orchestrator.store.active_model_invocations(session_id)) == 1
    orchestrator.leases.revoke_for_lifecycle(session_id, reason="test-crash")
    recovered = orchestrator.recover_session(session_id, worker_id="worker-recovery")
    assert len(recovered["interrupted_model_invocations"]) == 1
    assert orchestrator.store.active_model_invocations(session_id) == []
    retry_started = orchestrator.start(worker_id="worker-retry")
    retry = runner.run(
        session_id,
        retry_started["run_id"],
        retry_started["lease_token"] or "",
        idempotency_key="planner-crash-retry",
    )
    assert retry.status == "COMPLETED"
