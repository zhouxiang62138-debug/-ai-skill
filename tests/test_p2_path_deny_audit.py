from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.event_types import EventType
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
    LocalCompatibilityEnvironment,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "e" * 64
_ENV_HASH = "f" * 64


def _setup(tmp_path: Path, role: str = "generator"):
    project_root = tmp_path / "test_p2_project"
    (project_root / "code").mkdir(parents=True)
    database = tmp_path / "control-plane" / "runtime-test-p2" / "sessions.sqlite3"
    store = SessionStore(database)
    session = store.create_session(
        "test_p2_project", project_root, idempotency_key="p2-session"
    )
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "p2-worker")
    run_id = store.create_role_run(session.session_id, "p2-worker", role)
    context = ExecutionContext(
        session_id=session.session_id,
        run_id=run_id,
        worker_id="p2-worker",
        lease_version=lease.lease_version,
        role=role,
        project_id="test_p2_project",
        project_root=str(project_root),
    )
    environment = LocalCompatibilityEnvironment()
    environment.provision(context)
    broker = ExecutionBroker(store, leases)
    return store, lease, context, environment, broker


def _path_events(store: SessionStore, session_id: str):
    return [
        event
        for event in store.list_events(session_id)
        if event.event_type == EventType.PATH_ACCESS_DENIED
    ]


def _capability_events(store: SessionStore, session_id: str):
    return [
        event
        for event in store.list_events(session_id)
        if event.event_type == EventType.CAPABILITY_DENIED
    ]


def _lease_token(lease) -> str:
    return lease.lease_token or ""


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        logical_call_id="p2-execution",
        argv=(sys.executable, "-c", "print('ok')"),
        execution_profile=ExecutionProfile("p2", _CODE_HASH, _ENV_HASH),
        timeout=5,
    )


def test_project_yaml_direct_write_is_durable_and_deduplicated(tmp_path: Path) -> None:
    store, lease, context, environment, broker = _setup(tmp_path, "generator")

    for _ in range(2):
        with pytest.raises(RuntimeValidationError, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
            broker.write_file(
                context,
                environment,
                "project.yaml",
                "status: ACCEPTED\n",
                lease_token=_lease_token(lease),
            )

    events = _path_events(store, context.session_id)
    assert len(events) == 1
    assert events[0].payload["role"] == "generator"
    assert events[0].payload["operation"] == "write"
    assert events[0].payload["reason_code"] == "PROJECT_YAML_DIRECT_WRITE_DENIED"
    assert events[0].payload["decision"] == "DENY"

    reopened = SessionStore(store.path)
    durable = _path_events(reopened, context.session_id)
    assert len(durable) == 1
    assert durable[0].payload == events[0].payload
    assert durable[0].correlation_id == context.run_id


@pytest.mark.parametrize(
    ("role", "path", "error_code"),
    [
        ("evaluator", "code/main.py", "EXECUTION_PATH_PROHIBITED"),
        ("generator", "../outside.txt", "项目内相对路径"),
    ],
)
def test_role_and_project_escape_denials_are_audited(
    tmp_path: Path, role: str, path: str, error_code: str
) -> None:
    store, lease, context, environment, broker = _setup(tmp_path, role)

    with pytest.raises(RuntimeValidationError, match=error_code):
        broker.write_file(
            context,
            environment,
            path,
            "blocked-secret-content",
            lease_token=_lease_token(lease),
        )

    events = _path_events(store, context.session_id)
    assert len(events) == 1
    assert events[0].payload["role"] == role
    assert events[0].payload["operation"] == "write"
    assert events[0].payload["reason_code"] in {
        "ROLE_PATH_DENIED",
        "PATH_OUTSIDE_PROJECT",
    }
    serialized = json.dumps(events[0].payload, ensure_ascii=False)
    assert "blocked-secret-content" not in serialized
    assert str(Path(context.project_root)) not in serialized


def test_symlink_escape_is_audited_without_backend_store_dependency(tmp_path: Path) -> None:
    store, lease, context, environment, broker = _setup(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside", encoding="utf-8")
    link = Path(context.project_root) / "code" / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.fail(f"P2 symlink 真实验证不可用：{exc}")

    with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
        broker.read_file(
            context,
            environment,
            "code/outside-link.txt",
            lease_token=_lease_token(lease),
        )

    events = _path_events(store, context.session_id)
    assert len(events) == 1
    assert events[0].payload["reason_code"] == "SYMLINK_ESCAPE_DENIED"
    serialized = json.dumps(events[0].payload, ensure_ascii=False)
    assert "secret outside" not in serialized
    assert "outside-link.txt" in events[0].payload["resource_reference"]

    backend_source = (
        Path(__file__).parents[1] / "runtime" / "execution" / "base.py"
    ).read_text(encoding="utf-8")
    assert "SessionStore" not in backend_source
    assert "sqlite" not in backend_source.lower()


def test_windows_junction_escape_is_audited(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.fail("P2 Junction 真实验证需要 Windows 文件系统")
    store, lease, context, environment, broker = _setup(tmp_path)
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    target_file = outside / "secret.txt"
    target_file.write_text("junction secret", encoding="utf-8")
    junction = Path(context.project_root) / "code" / "outside-junction"
    create = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if create.returncode != 0:
        pytest.fail(f"P2 Junction 创建失败：{create.stderr or create.stdout}")

    try:
        assert junction.is_dir()
        assert junction != outside
        assert junction.resolve() == outside.resolve()
        with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
            broker.read_file(
                context,
                environment,
                "code/outside-junction/secret.txt",
                lease_token=_lease_token(lease),
            )
        events = _path_events(store, context.session_id)
        assert len(events) == 1
        assert events[0].payload["reason_code"] == "JUNCTION_ESCAPE_DENIED"
    finally:
        assert junction.exists() or junction.is_symlink()
        assert outside.is_dir()
        assert junction != outside
        cleanup = subprocess.run(
            ["cmd.exe", "/c", "rmdir", str(junction)],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cleanup.returncode == 0, cleanup.stderr or cleanup.stdout
        assert not junction.exists()
        assert outside.is_dir()
        assert target_file.read_text(encoding="utf-8") == "junction secret"


def test_control_plane_raw_access_is_denied_and_safely_referenced(tmp_path: Path) -> None:
    store, lease, context, environment, broker = _setup(tmp_path)

    with pytest.raises(RuntimeValidationError, match="项目内相对路径"):
        broker.read_file(
            context,
            environment,
            str(store.path),
            lease_token=_lease_token(lease),
        )

    events = _path_events(store, context.session_id)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["reason_code"] == "CONTROL_PLANE_ACCESS_DENIED"
    assert payload["resource_class"] == "control_plane"
    assert str(store.path) not in json.dumps(payload, ensure_ascii=False)


def test_allowed_generator_write_has_no_path_deny_event(tmp_path: Path) -> None:
    store, lease, context, environment, broker = _setup(tmp_path, "generator")

    broker.write_file(
        context,
        environment,
        "code/app.py",
        "print('ok')\n",
        lease_token=_lease_token(lease),
    )

    assert (Path(context.project_root) / "code" / "app.py").read_text(
        encoding="utf-8"
    ) == "print('ok')\n"
    assert _path_events(store, context.session_id) == []


def test_capability_deny_stops_before_path_audit(tmp_path: Path) -> None:
    store, lease, context, environment, broker = _setup(tmp_path, "planner")

    with pytest.raises(RuntimeValidationError, match="CAPABILITY_DENIED"):
        broker.execute(
            context,
            _request(),
            environment,
            lease_token=_lease_token(lease),
        )

    assert len(_capability_events(store, context.session_id)) == 1
    assert _path_events(store, context.session_id) == []


def test_audit_persistence_failure_remains_a_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, lease, context, environment, broker = _setup(tmp_path, "generator")

    def fail_append(*args, **kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(store, "append_event", fail_append)
    with pytest.raises(RuntimeStorageError, match="PATH_DENIAL_AUDIT_PERSISTENCE_FAILED"):
        broker.write_file(
            context,
            environment,
            "project.yaml",
            "status: ACCEPTED\n",
            lease_token=_lease_token(lease),
        )
    assert not (Path(context.project_root) / "project.yaml").exists()
