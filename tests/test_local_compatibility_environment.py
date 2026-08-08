"""F11.2 LocalCompatibilityEnvironment 验收。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from runtime.errors import RuntimeValidationError
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
    LocalCompatibilityEnvironment,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "c" * 64
_ENV_HASH = "d" * 64


def _context(root: Path, role: str = "generator") -> ExecutionContext:
    return ExecutionContext(
        session_id="session-local",
        run_id="run-local",
        worker_id="worker-local",
        lease_version=1,
        role=role,
        project_id="project-local",
        project_root=str(root),
    )


def _request(
    argv: tuple[str, ...], *, logical_call_id: str = "local-call", cwd: str = ".", timeout: float = 5
) -> ExecutionRequest:
    return ExecutionRequest(
        logical_call_id=logical_call_id,
        argv=argv,
        cwd=cwd,
        timeout=timeout,
        execution_profile=ExecutionProfile("compatibility", _CODE_HASH, _ENV_HASH),
    )


def _environment(tmp_path: Path, role: str = "generator") -> tuple[Path, ExecutionContext, LocalCompatibilityEnvironment]:
    root = tmp_path / "workspace"
    (root / "code").mkdir(parents=True)
    context = _context(root, role)
    environment = LocalCompatibilityEnvironment()
    environment.provision(context)
    return root, context, environment


def test_shell_false_stdin_devnull_and_streaming_capture(monkeypatch, tmp_path: Path) -> None:
    _, context, environment = _environment(tmp_path)
    captured: dict[str, object] = {}
    original_popen = __import__("runtime.execution.local", fromlist=["subprocess"]).subprocess.Popen

    def spy_popen(*args, **kwargs):
        captured.update(kwargs)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr("runtime.execution.local.subprocess.Popen", spy_popen)
    result = environment.execute(
        context, _request((sys.executable, "-c", "print('ok')"))
    )

    assert result.exit_code == 0
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert "capture_output" not in captured


def test_shell_command_string_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeValidationError, match="参数数组"):
        _request("python -c print('bad')")  # type: ignore[arg-type]


def test_command_profile_rejects_shell_interpreters_by_default(tmp_path: Path) -> None:
    _, context, environment = _environment(tmp_path)
    with pytest.raises(RuntimeValidationError, match="LOCAL_COMMAND_NOT_ALLOWED"):
        environment.execute(context, _request(("cmd.exe", "/c", "echo", "bad")))


def test_absolute_parent_and_cwd_escape_are_rejected(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(RuntimeValidationError, match="项目内相对路径"):
        environment.read_file(context, str(outside))
    with pytest.raises(RuntimeValidationError, match="项目内相对路径"):
        environment.write_file(context, "../outside.txt", "bad")
    with pytest.raises(RuntimeValidationError, match="项目内相对路径"):
        environment.execute(
            context,
            _request((sys.executable, "-c", "print('bad')"), cwd=".."),
        )
    assert not (root / "outside.txt").exists()


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "code" / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境无 symlink 权限：{exc}")

    with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
        environment.read_file(context, "code/outside-link.txt")


@pytest.mark.skipif(os.name != "nt", reason="Junction 只在 Windows 验证")
def test_windows_junction_escape_is_rejected(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    junction = root / "code" / "outside-junction"
    mklink_command = [
        "cmd.exe",
        "/c",
        "mklink",
        "/J",
        str(junction),
        str(outside),
    ]
    created = False
    try:
        result = subprocess.run(
            mklink_command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        pytest.skip(
            f"Junction command={mklink_command!r}; OSError={exc!r}"
        )
    if result.returncode != 0:
        pytest.skip(
            "Junction 创建失败；"
            f"command={mklink_command!r}; exit_code={result.returncode}; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
    created = True
    try:
        with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
            environment.read_file(context, "code/outside-junction/secret.txt")
        with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
            environment.write_file(
                context, "code/outside-junction/new.txt", "must-not-write"
            )
        with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
            environment.list_files(context, "code/outside-junction")
        with pytest.raises(RuntimeValidationError, match="路径解析逃出项目根目录"):
            environment.execute(
                context,
                _request(
                    (sys.executable, "-c", "print('must-not-execute')"),
                    cwd="code/outside-junction",
                ),
            )
    finally:
        if created:
            cleanup_command = [
                "cmd.exe",
                "/c",
                "rmdir",
                str(junction),
            ]
            cleanup = subprocess.run(
                cleanup_command,
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if cleanup.returncode != 0:
                pytest.fail(
                    "Junction cleanup failed; "
                    f"command={cleanup_command!r}; exit_code={cleanup.returncode}; "
                    f"stdout={cleanup.stdout!r}; stderr={cleanup.stderr!r}"
                )
            if not outside.is_dir():
                pytest.fail("Junction cleanup unexpectedly removed the real target")


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("generator", "project.yaml"),
        ("planner", "code/main.py"),
        ("evaluator", "code/main.py"),
    ],
)
def test_role_and_project_state_write_policy(
    tmp_path: Path, role: str, path: str
) -> None:
    _, context, environment = _environment(tmp_path, role)
    expected = (
        "PROJECT_STATE_WRITE_REQUIRES_CAS"
        if path == "project.yaml"
        else "EXECUTION_PATH_PROHIBITED"
    )
    with pytest.raises(RuntimeValidationError, match=expected):
        environment.write_file(context, path, "bad")


def test_list_files_uses_the_same_path_policy(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    (root / "code" / "main.py").write_text("ok", encoding="utf-8")
    assert environment.list_files(context, "code") == ["code/main.py"]
    with pytest.raises(RuntimeValidationError, match="项目内相对路径"):
        environment.list_files(context, "../")


def test_timeout_terminates_process(tmp_path: Path) -> None:
    _, context, environment = _environment(tmp_path)
    result = environment.execute(
        context,
        _request(
            (sys.executable, "-c", "import time; time.sleep(5)"),
            timeout=0.2,
        ),
    )
    assert result.timed_out is True
    assert result.exit_code is None
    assert environment.last_cleanup_status in {"ALREADY_EXITED", "TERMINATED"}


def test_timeout_terminates_child_process_tree(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    marker = root / "child-marker.txt"
    child_code = (
        "import pathlib,time; time.sleep(3); "
        f"pathlib.Path({json.dumps(str(marker))}).write_text('child-alive')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
        "time.sleep(30)"
    )
    result = environment.execute(
        context,
        _request((sys.executable, "-c", parent_code), timeout=0.2),
    )
    assert result.timed_out is True
    assert environment.last_cleanup_status == "TERMINATED"
    time.sleep(0.7)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree 验证")
def test_timeout_terminates_grandchild_process_tree(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    marker = root / "grandchild-marker.txt"
    grandchild_code = (
        "import pathlib,time; time.sleep(2); "
        f"pathlib.Path({json.dumps(str(marker))}).write_text('grandchild-alive')"
    )
    child_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(grandchild_code)}]); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
        "time.sleep(30)"
    )
    result = environment.execute(
        context,
        _request((sys.executable, "-c", parent_code), timeout=0.2),
    )
    assert result.timed_out is True
    assert environment.last_cleanup_status == "TERMINATED"
    time.sleep(0.7)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree 验证")
def test_parent_exit_race_is_cleanup_safe(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    marker = root / "parent-exit-race-marker.txt"
    child_code = (
        "import pathlib,time; time.sleep(1); "
        f"pathlib.Path({json.dumps(str(marker))}).write_text('child-alive')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
        "time.sleep(0.08)"
    )
    result = environment.execute(
        context,
        _request((sys.executable, "-c", parent_code), timeout=0.05),
    )
    assert result.timed_out is True
    assert environment.last_cleanup_status in {"ALREADY_EXITED", "TERMINATED"}
    time.sleep(0.7)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree 验证")
def test_child_exit_during_cleanup_is_idempotent(tmp_path: Path) -> None:
    root, context, environment = _environment(tmp_path)
    started = root / "child-started.txt"
    finished = root / "child-finished.txt"
    child_code = (
        f"import pathlib,time; pathlib.Path({json.dumps(str(started))}).write_text('started'); "
        "time.sleep(0.05); "
        f"pathlib.Path({json.dumps(str(finished))}).write_text('finished')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
        "time.sleep(30)"
    )
    result = environment.execute(
        context,
        _request((sys.executable, "-c", parent_code), timeout=0.05),
    )
    assert result.timed_out is True
    assert environment.last_cleanup_status in {"ALREADY_EXITED", "TERMINATED"}
    time.sleep(0.4)
    assert not (root / "child-after-cleanup.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree 验证")
def test_repeated_windows_termination_is_idempotent(tmp_path: Path) -> None:
    from runtime.execution.local import _WindowsJobObject

    root, context, environment = _environment(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=str(root),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ),
    )
    job = _WindowsJobObject()
    environment._active_windows_job = job
    job.assign(process)
    first = environment._terminate_process_tree(process)
    second = environment._terminate_process_tree(process)
    job.close()
    environment._active_windows_job = None
    assert first == "TERMINATED"
    assert second == "ALREADY_EXITED"
    assert process.poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree 验证")
def test_timeout_process_tree_stress_20_rounds(tmp_path: Path) -> None:
    for iteration in range(20):
        root, context, environment = _environment(tmp_path / f"round-{iteration}")
        marker = root / "stress-child-marker.txt"
        child_code = (
            "import pathlib,time; time.sleep(1); "
            f"pathlib.Path({json.dumps(str(marker))}).write_text('child-alive')"
        )
        parent_code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
            "time.sleep(30)"
        )
        result = environment.execute(
            context,
            _request(
                (sys.executable, "-c", parent_code),
                logical_call_id=f"stress-{iteration}",
                timeout=0.2,
            ),
        )
        assert result.timed_out is True
        assert environment.last_cleanup_status == "TERMINATED"
        time.sleep(0.25)
        assert not marker.exists()


def test_stdout_and_stderr_are_bounded_and_marked(tmp_path: Path) -> None:
    _, context, environment = _environment(tmp_path)
    code = (
        "import sys; sys.stdout.write('o'*2000000); "
        "sys.stderr.write('e'*2000000)"
    )
    result = environment.execute(
        context, _request((sys.executable, "-c", code), timeout=10)
    )
    assert result.output_truncated is True
    assert len(result.stdout.encode("utf-8")) <= environment.output_limit_bytes
    assert len(result.stderr.encode("utf-8")) <= environment.output_limit_bytes


def test_sensitive_environment_values_are_not_forwarded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TOKEN", "secret-token")
    monkeypatch.setenv("SECRET", "secret-value")
    _, context, environment = _environment(tmp_path)
    result = environment.execute(
        context,
        _request((sys.executable, "-c", "import os; print(sorted(os.environ))")),
    )
    assert "TOKEN" not in result.stdout
    assert "SECRET" not in result.stdout


def test_local_backend_integrates_with_f11_broker(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = SessionStore(tmp_path / "sessions.sqlite3")
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
    environment = LocalCompatibilityEnvironment()
    environment.provision(context)
    broker = ExecutionBroker(store, leases)
    receipt = broker.execute(
        context,
        _request((sys.executable, "-c", "print('broker-ok')")),
        environment,
        lease_token=lease.lease_token or "",
    )
    assert receipt.status == "SUCCEEDED"
    assert receipt.result.stdout.strip() == "broker-ok"
    assert len(
        [
            event
            for event in store.list_events(session.session_id)
            if event.event_type
            in {"TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED", "TOOL_CALL_TIMED_OUT"}
        ]
    ) == 1


def test_local_backend_timeout_preserves_broker_timed_out_lifecycle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", root, idempotency_key="session-timeout")
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "worker-timeout")
    run_id = store.create_role_run(session.session_id, "worker-timeout", "generator")
    context = ExecutionContext(
        session_id=session.session_id,
        run_id=run_id,
        worker_id="worker-timeout",
        lease_version=lease.lease_version,
        role="generator",
        project_id="demo",
        project_root=str(root),
    )
    environment = LocalCompatibilityEnvironment()
    environment.provision(context)
    broker = ExecutionBroker(store, leases)
    receipt = broker.execute(
        context,
        _request(
            (sys.executable, "-c", "import time; time.sleep(5)"),
            logical_call_id="broker-timeout",
            timeout=0.2,
        ),
        environment,
        lease_token=lease.lease_token or "",
    )
    assert receipt.status == "TIMED_OUT"
    assert receipt.result.timed_out is True
    assert receipt.result.exit_code is None
    terminal = [
        event
        for event in store.list_events(session.session_id)
        if event.event_type
        in {"TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED", "TOOL_CALL_TIMED_OUT"}
    ]
    assert len(terminal) == 1
    assert terminal[0].event_type == "TOOL_CALL_TIMED_OUT"
