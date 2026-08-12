from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import change_request as change_request_module
from change_request import create_change_request, load_events
from runtime.control_plane import initialize_control_plane
from runtime.errors import LeaseError, RuntimeStorageError, RuntimeValidationError, StateConflictError
from runtime.event_types import EventType
from runtime.orchestrator import Orchestrator
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from tests.test_change_request import accepted_state


PROJECT_ID = "test_p3_change_request_app"


def _prepare_project(tmp_path: Path) -> tuple[Path, Path, SessionStore, Orchestrator]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane(PROJECT_ID, home=control_home)
    store = SessionStore(control_plane / "sessions.sqlite3")
    session = store.create_session(PROJECT_ID, root, idempotency_key="p3-session")
    state = accepted_state()
    state["project_id"] = PROJECT_ID
    state = preview_runtime_migration(
        state, project_root=root, session_id=session.session_id
    )
    (root / "memory" / "plans").mkdir(parents=True)
    (root / "memory" / "plans" / "plan-001.md").write_text(
        "# Approved Plan\n", encoding="utf-8"
    )
    (root / "evaluation" / "reports").mkdir(parents=True)
    (root / "evaluation" / "reports" / "evaluation-001.md").write_text(
        "# PASS\n", encoding="utf-8"
    )
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    return root, control_home, store, Orchestrator(
        root, control_plane_home=control_home
    )


def _create(root: Path, **kwargs: object) -> dict:
    options = {
        "raw_feedback": "增加任务完成/未完成状态",
        "requested_changes": ["增加任务完成状态并在列表中显示"],
    }
    options.update(kwargs)
    return create_change_request(root, **options)


def _session_id(root: Path) -> str:
    return str(load_project_state(root / "project.yaml")["runtime"]["session_id"])


def _open_patch() -> dict[str, object]:
    return {
        "status": "CHANGE_REQUESTED",
        "next_role": "planner",
        "active_module": None,
        "active_change_request": "CR-9999",
        "change_context": {
            "previous_project_status": "ACCEPTED",
            "change_cycle": 1,
            "evaluation_iteration": 0,
        },
        "change_cycle": 1,
        "current_iteration": 0,
        "automatic_retry_allowed": True,
    }


def test_accepted_to_change_request_uses_cas_and_durable_audit(tmp_path: Path) -> None:
    root, _, store, runtime = _prepare_project(tmp_path)

    result = _create(root, runtime=runtime, worker_id="p3-worker")
    state = load_project_state(root / "project.yaml")
    events = store.list_events(_session_id(root))
    audit = [event for event in events if event.event_type == EventType.CHANGE_REQUEST_CREATED]

    assert result["result"] == "PASS"
    assert state["project_id"] == PROJECT_ID
    assert state["status"] == "CHANGE_REQUESTED"
    assert state["next_role"] == "planner"
    assert state["change_request_record"] == result["request_path"].split(
        f"{PROJECT_ID}\\", 1
    )[-1].replace("\\", "/")
    assert (root / "memory" / "plans" / "plan-001.md").read_text(
        encoding="utf-8"
    ) == "# Approved Plan\n"
    assert (root / "evaluation" / "reports" / "evaluation-001.md").read_text(
        encoding="utf-8"
    ) == "# PASS\n"
    assert Path(result["request_path"]).is_file()
    assert len(audit) == 1
    assert audit[0].payload["change_request_id"] == result["change_request_id"]
    assert audit[0].payload["base_revision"] == 0
    assert audit[0].payload["new_revision"] == state["runtime"]["revision"]
    assert "完成/未完成" not in str(audit[0].payload)
    assert len(load_events(root, result["change_request_id"])) == 1
    reopened = SessionStore(store.path)
    reopened_audit = [
        event
        for event in reopened.list_events(_session_id(root))
        if event.event_type == EventType.CHANGE_REQUEST_CREATED
    ]
    assert len(reopened_audit) == 1
    assert reopened_audit[0].payload == audit[0].payload


def test_minimal_e2e_reaches_planner_impact_analysis_entry(tmp_path: Path) -> None:
    root, _, _, runtime = _prepare_project(tmp_path)

    _create(root, runtime=runtime, worker_id="p3-worker")
    selection = runtime.start(worker_id="p3-planner")

    assert selection["selection"].kind == "ROLE"
    assert selection["selection"].target == "planner"
    assert load_project_state(root / "project.yaml")["status"] == "CHANGE_REQUESTED"


def test_v7_path_does_not_use_legacy_project_writer(tmp_path: Path, monkeypatch) -> None:
    root, _, _, runtime = _prepare_project(tmp_path)

    def fail_direct_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("v7 Change Request 不能直接写 project.yaml")

    monkeypatch.setattr(
        change_request_module, "write_project_state_atomic", fail_direct_write
    )
    result = _create(root, runtime=runtime)

    assert result["project_status"] == "CHANGE_REQUESTED"


def test_cas_failure_keeps_state_and_no_formal_change_request(tmp_path: Path, monkeypatch) -> None:
    root, _, store, runtime = _prepare_project(tmp_path)
    before = (root / "project.yaml").read_bytes()

    def fail_cas(*args: object, **kwargs: object) -> None:
        raise StateConflictError("stale revision")

    monkeypatch.setattr(runtime.cas, "commit_patch", fail_cas)
    with pytest.raises(StateConflictError):
        _create(root, runtime=runtime)

    assert (root / "project.yaml").read_bytes() == before
    assert not list((root / "change_requests").glob("CR-*.yaml"))
    assert not [
        event
        for event in store.list_events(_session_id(root))
        if event.event_type == EventType.CHANGE_REQUEST_CREATED
    ]
    assert list((root / "change_requests" / ".staging").glob("create-*.yaml"))


def test_retry_is_idempotent_and_does_not_duplicate_formal_audit(tmp_path: Path) -> None:
    root, _, store, runtime = _prepare_project(tmp_path)

    first = _create(root, runtime=runtime)
    second = _create(root, runtime=runtime)
    events = store.list_events(_session_id(root))
    audit = [event for event in events if event.event_type == EventType.CHANGE_REQUEST_CREATED]

    assert second["change_request_id"] == first["change_request_id"]
    assert second["request_path"] == first["request_path"]
    assert load_project_state(root / "project.yaml")["runtime"]["revision"] == 2
    assert len(list((root / "change_requests").glob("CR-*.yaml"))) == 1
    assert len(audit) == 1


def test_crash_after_formal_state_commit_recovers_on_retry(tmp_path: Path, monkeypatch) -> None:
    root, _, store, runtime = _prepare_project(tmp_path)
    original_append = runtime.store.append_event
    failed = {"value": False}

    def fail_created_event(*args: object, **kwargs: object):
        event_type = args[1] if len(args) > 1 else kwargs.get("event_type")
        if event_type == EventType.CHANGE_REQUEST_CREATED and not failed["value"]:
            failed["value"] = True
            raise RuntimeStorageError("simulated finalization crash")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(runtime.store, "append_event", fail_created_event)
    with pytest.raises(RuntimeStorageError):
        _create(root, runtime=runtime)

    state_after_crash = load_project_state(root / "project.yaml")
    assert state_after_crash["status"] == "CHANGE_REQUESTED"
    assert state_after_crash["change_request_record"] is not None

    result = _create(root, runtime=runtime)
    audit = [
        event
        for event in store.list_events(_session_id(root))
        if event.event_type == EventType.CHANGE_REQUEST_CREATED
    ]
    assert result["result"] == "PASS"
    assert len(audit) == 1


def test_stale_revision_and_invalid_transition_are_denied(tmp_path: Path) -> None:
    root, _, _, runtime = _prepare_project(tmp_path)
    session_id = _session_id(root)

    with pytest.raises(StateConflictError):
        runtime.commit_module_step(
            session_id,
            "change_request",
            {
                "project_yaml": root / "project.yaml",
                "source_status": "ACCEPTED",
                "target_status": "CHANGE_REQUESTED",
                "changed_fields": _open_patch(),
                "expected_revision": 9,
                "idempotency_key": "p3-stale-revision",
            },
            worker_id="p3-worker",
        )

    with pytest.raises(RuntimeValidationError, match="ILLEGAL_STATE_TRANSITION"):
        runtime.commit_module_step(
            session_id,
            "change_request",
            {
                "project_yaml": root / "project.yaml",
                "source_status": "ACCEPTED",
                "target_status": "PLANNING",
                "changed_fields": {"status": "PLANNING"},
                "expected_revision": 0,
                "idempotency_key": "p3-invalid-transition",
            },
            worker_id="p3-worker",
        )


def test_stale_lease_and_role_field_mutation_are_denied(tmp_path: Path) -> None:
    root, _, _, runtime = _prepare_project(tmp_path)
    session_id = _session_id(root)
    lease = runtime.leases.acquire(session_id, "p3-stale-lease")
    runtime.leases.release(
        session_id,
        "p3-stale-lease",
        lease.lease_version,
        lease.lease_token or "",
    )

    with pytest.raises(LeaseError):
        runtime.cas.commit_patch(
            root / "project.yaml",
            _open_patch(),
            source_status="ACCEPTED",
            target_status="CHANGE_REQUESTED",
            session_id=session_id,
            worker_id="p3-stale-lease",
            actor_role="change_request",
            lease_version=lease.lease_version,
            lease_token=lease.lease_token or "",
            expected_revision=0,
            idempotency_key="p3-stale-lease-commit",
        )

    lease = runtime.leases.acquire(session_id, "p3-owner")
    bad_fields = _open_patch()
    bad_fields["plan_status"] = "not_started"
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        runtime.cas.commit_patch(
            root / "project.yaml",
            bad_fields,
            source_status="ACCEPTED",
            target_status="CHANGE_REQUESTED",
            session_id=session_id,
            worker_id="p3-owner",
            actor_role="change_request",
            lease_version=lease.lease_version,
            lease_token=lease.lease_token or "",
            expected_revision=0,
            idempotency_key="p3-role-field-mutation",
        )
