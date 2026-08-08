"""F11.4 Local Snapshot / Restore 与 Execution Recovery 测试。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from runtime.errors import LeaseError, RuntimeValidationError
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
    LocalCompatibilityEnvironment,
    WorkspaceSnapshotService,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "f" * 64
_ENV_HASH = "a" * 64


def _setup(tmp_path: Path):
    root = tmp_path / "workspace"
    (root / "code").mkdir(parents=True)
    control = tmp_path / "control-plane"
    store = SessionStore(control / "sessions.sqlite3")
    session = store.create_session("demo", root, idempotency_key="session")
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "worker-a")
    run_id = store.create_role_run(session.session_id, "worker-a", "generator")
    context = ExecutionContext(
        session_id=session.session_id,
        run_id=run_id,
        worker_id="worker-a",
        lease_version=lease.lease_version,
        role="generator",
        project_id="demo",
        project_root=str(root),
    )
    snapshots = WorkspaceSnapshotService(control / "snapshots")
    environment = LocalCompatibilityEnvironment(snapshot_service=snapshots)
    environment.provision(context)
    broker = ExecutionBroker(store, leases, snapshot_service=snapshots)
    return root, control, store, leases, lease, context, environment, broker


def _request(logical_call_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        logical_call_id=logical_call_id,
        argv=(sys.executable, "-c", "print('recovery-ok')"),
        cwd=".",
        timeout=5,
        execution_profile=ExecutionProfile("compatibility", _CODE_HASH, _ENV_HASH),
    )


def test_snapshot_is_binary_safe_persistent_and_outside_workspace(tmp_path: Path) -> None:
    root, control, _, _, lease, context, environment, broker = _setup(tmp_path)
    text_file = root / "code" / "note.txt"
    binary_file = root / "code" / "bytes.bin"
    text_file.write_text("before restore", encoding="utf-8")
    binary = bytes(range(256))
    binary_file.write_bytes(binary)

    snapshot_id = broker.snapshot(
        context, environment, lease_token=lease.lease_token or ""
    )
    snapshot = control / "snapshots" / snapshot_id
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))

    assert snapshot.is_dir()
    assert not (root / snapshot_id).exists()
    assert manifest["session_id"] == context.session_id
    assert manifest["run_id"] == context.run_id
    assert len(manifest["workspace_hash"]) == 64
    assert {entry["path"] for entry in manifest["files"]} >= {
        "code/note.txt",
        "code/bytes.bin",
    }

    text_file.write_text("changed", encoding="utf-8")
    binary_file.unlink()
    (root / "code" / "new.txt").write_text("must be removed", encoding="utf-8")
    broker.restore(
        context,
        environment,
        snapshot_id,
        lease_token=lease.lease_token or "",
    )

    assert text_file.read_text(encoding="utf-8") == "before restore"
    assert binary_file.read_bytes() == binary
    assert not (root / "code" / "new.txt").exists()


def test_snapshot_does_not_include_control_plane_or_secrets(tmp_path: Path) -> None:
    root, control, _, _, lease, context, environment, broker = _setup(tmp_path)
    (root / ".runtime").mkdir()
    (root / ".runtime" / "sessions.sqlite3").write_bytes(b"sqlite-control-plane")
    (root / "project.yaml").write_text("status: CONTROL_PLANE", encoding="utf-8")
    (root / "tool-results").mkdir()
    (root / "tool-results" / "result.json").write_text("result", encoding="utf-8")
    (root / "locks").mkdir()
    (root / "locks" / "lease").write_text("lock", encoding="utf-8")
    (root / "code" / "secret.env").write_text(
        "TOKEN=host-secret\n", encoding="utf-8"
    )

    snapshot_id = broker.snapshot(
        context, environment, lease_token=lease.lease_token or ""
    )
    snapshot = control / "snapshots" / snapshot_id
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    stored_bytes = b"".join(
        item.read_bytes()
        for item in snapshot.rglob("*")
        if item.is_file()
    )

    assert all(not path.startswith(".runtime/") for path in paths)
    assert "project.yaml" not in paths
    assert all(not path.startswith("tool-results/") for path in paths)
    assert all(not path.startswith("locks/") for path in paths)
    assert "code/secret.env" not in paths
    assert b"sqlite-control-plane" not in stored_bytes
    assert b"host-secret" not in stored_bytes


def test_tampered_snapshot_is_rejected_without_changing_workspace(tmp_path: Path) -> None:
    root, control, _, _, lease, context, environment, broker = _setup(tmp_path)
    target = root / "code" / "note.txt"
    target.write_text("trusted", encoding="utf-8")
    snapshot_id = broker.snapshot(
        context, environment, lease_token=lease.lease_token or ""
    )
    stored = control / "snapshots" / snapshot_id / "files" / "code" / "note.txt"
    stored.write_text("tampered", encoding="utf-8")
    target.write_text("current", encoding="utf-8")

    with pytest.raises(RuntimeValidationError, match="RESTORE_VERIFICATION_FAILED"):
        broker.restore(
            context,
            environment,
            snapshot_id,
            lease_token=lease.lease_token or "",
        )
    assert target.read_text(encoding="utf-8") == "current"


def test_missing_snapshot_is_rejected(tmp_path: Path) -> None:
    _, _, _, _, lease, context, environment, broker = _setup(tmp_path)
    with pytest.raises(RuntimeValidationError, match="SNAPSHOT_MISSING"):
        broker.restore(
            context,
            environment,
            "snapshot-missing",
            lease_token=lease.lease_token or "",
        )


def test_stale_lease_cannot_restore(tmp_path: Path) -> None:
    root, _, store, leases, lease, context, environment, broker = _setup(tmp_path)
    (root / "code" / "note.txt").write_text("trusted", encoding="utf-8")
    snapshot_id = broker.snapshot(
        context, environment, lease_token=lease.lease_token or ""
    )
    leases.revoke_for_lifecycle(context.session_id, reason="stale-worker-test")
    replacement = leases.acquire(context.session_id, "worker-b")
    assert replacement.worker_id == "worker-b"

    with pytest.raises(LeaseError):
        broker.restore(
            context,
            environment,
            snapshot_id,
            lease_token=lease.lease_token or "",
        )
    connection = store.raw_connection()
    connection.close()


def test_started_tool_crash_remains_unknown_and_is_not_replayed(tmp_path: Path) -> None:
    _, _, store, _, lease, context, environment, broker = _setup(tmp_path)
    request = _request("crash-call")
    profile = request.resolved_profile()
    arguments = {
        "logical_call_id": request.logical_call_id,
        "argv": list(request.argv),
        "cwd": request.cwd,
        "timeout": request.timeout,
        "execution_profile": profile.name,
        "code_snapshot_hash": profile.code_snapshot_hash,
        "environment_hash": profile.environment_hash,
    }
    call = store.request_tool_call(
        context.session_id,
        tool_name="execution",
        arguments=arguments,
        idempotency_key="execution:crash-call",
    )
    store.start_tool_call(
        context.session_id,
        call,
        code_snapshot_hash=profile.code_snapshot_hash,
        environment_hash=profile.environment_hash,
    )
    assert store.recover_interrupted_tool_calls(context.session_id) == [call]

    with pytest.raises(RuntimeValidationError, match="TOOL_CALL_REQUIRES_RECOVERY"):
        broker.execute(
            context,
            request,
            environment,
            lease_token=lease.lease_token or "",
        )


def test_deleted_workspace_restores_then_continues_execution(tmp_path: Path) -> None:
    root, _, _, _, lease, context, environment, broker = _setup(tmp_path)
    (root / "code" / "note.txt").write_text("continue", encoding="utf-8")
    snapshot_id = broker.snapshot(
        context, environment, lease_token=lease.lease_token or ""
    )
    environment.terminate(context)
    shutil.rmtree(root)

    broker.restore(
        context,
        environment,
        snapshot_id,
        lease_token=lease.lease_token or "",
    )
    receipt = broker.execute(
        context,
        _request("after-restore"),
        environment,
        lease_token=lease.lease_token or "",
    )

    assert receipt.status == "SUCCEEDED"
    assert receipt.result.stdout.strip() == "recovery-ok"
    assert (root / "code" / "note.txt").read_text(encoding="utf-8") == "continue"


def test_snapshot_backend_has_no_control_plane_dependency() -> None:
    for name in ("snapshots.py", "local.py"):
        source = Path(__file__).parents[1] / "runtime" / "execution" / name
        text = source.read_text(encoding="utf-8").lower()
        assert "from runtime.session_store" not in text
        assert "import sessionstore" not in text
        assert "import sqlite3" not in text
        assert "from sqlite3" not in text
