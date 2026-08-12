"""F11.3A 真实 Docker Container 集成测试。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from runtime.execution import (
    DockerExecutionEnvironment,
    ExecutionBroker,
    ExecutionContext,
    ExecutionProfile,
    ExecutionRequest,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "e" * 64


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker command unavailable")
    result = subprocess.run(
        ["docker", "info", "--format={{.ServerVersion}}"],
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(
            "Docker daemon unavailable; "
            f"exit_code={result.returncode}; stderr={result.stderr.strip()}"
        )


def _context(root: Path, role: str = "generator") -> ExecutionContext:
    return ExecutionContext(
        session_id="docker-session",
        run_id="docker-run",
        worker_id="docker-worker",
        lease_version=1,
        role=role,
        project_id="docker-project",
        project_root=str(root),
    )


def _request(
    environment: DockerExecutionEnvironment,
    argv: tuple[str, ...],
    *,
    logical_call_id: str = "docker-call",
    cwd: str = ".",
    timeout: float = 30,
) -> ExecutionRequest:
    return ExecutionRequest(
        logical_call_id=logical_call_id,
        argv=argv,
        cwd=cwd,
        timeout=timeout,
        execution_profile=ExecutionProfile(
            "default", _CODE_HASH, environment.environment_hash_for("default")
        ),
    )


@pytest.fixture
def docker_setup(tmp_path: Path):
    _require_docker()
    root = tmp_path / "project"
    (root / "code").mkdir(parents=True)
    (root / ".runtime").mkdir()
    (root / ".runtime" / "sessions.sqlite3").write_text(
        "control-plane", encoding="utf-8"
    )
    other = tmp_path / "other-project"
    other.mkdir()
    (other / "secret.txt").write_text("other-project-secret", encoding="utf-8")
    environment = DockerExecutionEnvironment()
    context = _context(root)
    environment.provision(context)
    try:
        yield root, other, context, environment
    finally:
        if environment.container_id is not None:
            environment.terminate(context)


def test_docker_backend_is_decoupled_from_control_plane() -> None:
    source = Path(__file__).parents[1] / "runtime" / "execution" / "docker.py"
    text = source.read_text(encoding="utf-8")
    assert "SessionStore" not in text
    assert "sqlite" not in text.lower()
    assert "LocalCompatibilityEnvironment" not in text


def test_command_runs_inside_real_container_and_uses_shell_false(
    docker_setup, monkeypatch
) -> None:
    _, _, context, environment = docker_setup
    module = __import__("runtime.execution.docker", fromlist=["subprocess"])
    original_popen = module.subprocess.Popen
    calls: list[dict[str, object]] = []

    def spy_popen(*args, **kwargs):
        calls.append(kwargs)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", spy_popen)
    result = environment.execute(
        context,
        _request(
            environment,
            (
                "python",
                "-c",
                "import os; "
                "print('container=' + str(os.path.exists('/.dockerenv'))); "
                "print('control=' + str(os.path.exists('/workspace/.runtime/sessions.sqlite3'))); "
                "print('socket=' + str(os.path.exists('/var/run/docker.sock')))",
            ),
        ),
    )
    assert result.exit_code == 0
    assert "container=True" in result.stdout
    assert "control=False" in result.stdout
    assert "socket=False" in result.stdout
    assert calls
    assert all(call["shell"] is False for call in calls)


def test_container_policy_and_workspace_mount_are_real(docker_setup) -> None:
    root, other, _, environment = docker_setup
    assert environment.container_id is not None
    inspected = subprocess.run(
        ["docker", "inspect", environment.container_id],
        check=True,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(inspected.stdout)[0]
    host_config = payload["HostConfig"]
    assert host_config["NetworkMode"] == "none"
    assert host_config["Privileged"] is False
    assert host_config["PidMode"] in (None, "")
    assert host_config["IpcMode"] in (None, "")
    assert host_config["PidsLimit"] == 128
    assert host_config["Memory"] == 512 * 1024 * 1024
    assert host_config["NanoCpus"] == 1_000_000_000
    assert "ALL" in host_config["CapDrop"]
    assert "no-new-privileges:true" in host_config["SecurityOpt"]

    mounts = payload["Mounts"]
    bind_mounts = [mount for mount in mounts if mount["Destination"] == "/workspace"]
    assert len(bind_mounts) == 1
    assert Path(bind_mounts[0]["Source"]).resolve() == root.resolve()
    destinations = {mount["Destination"] for mount in mounts}
    assert "/workspace/.runtime" in destinations
    assert all("docker.sock" not in str(mount).lower() for mount in mounts)
    sources = {
        Path(mount["Source"]).resolve()
        for mount in mounts
        if "Source" in mount
    }
    assert other.resolve() not in sources
    assert Path.home().resolve() not in sources


def test_environment_hash_is_real_and_host_secrets_are_not_injected(
    docker_setup, monkeypatch
) -> None:
    _, _, context, environment = docker_setup
    monkeypatch.setenv("TOKEN", "host-secret-token")
    result = environment.execute(
        context,
        _request(
            environment,
            ("python", "-c", "import os; print('TOKEN' in os.environ)"),
        ),
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "False"
    assert re.fullmatch(r"[0-9a-f]{64}", environment.environment_hash)
    assert environment.environment_hash != "unknown"


def test_terminate_kills_and_removes_real_container(docker_setup) -> None:
    _, _, context, environment = docker_setup
    container_id = environment.container_id
    assert container_id
    environment.terminate(context)
    assert environment.container_id is None
    inspected = subprocess.run(
        ["docker", "inspect", container_id],
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert inspected.returncode != 0


def test_real_container_exec_integrates_with_f11_broker(
    docker_setup, tmp_path: Path
) -> None:
    root, _, _, environment = docker_setup
    store = SessionStore(tmp_path / "control.sqlite3")
    session = store.create_session("docker-project", root, idempotency_key="docker-session")
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "docker-worker")
    run_id = store.create_role_run(session.session_id, "docker-worker", "generator")
    context = ExecutionContext(
        session_id=session.session_id,
        run_id=run_id,
        worker_id="docker-worker",
        lease_version=lease.lease_version,
        role="generator",
        project_id="docker-project",
        project_root=str(root),
    )
    broker = ExecutionBroker(store, leases)
    receipt = broker.execute(
        context,
        _request(environment, ("python", "-c", "print('docker-broker-ok')")),
        environment,
        lease_token=lease.lease_token or "",
    )
    assert receipt.status == "SUCCEEDED"
    assert receipt.result.stdout.strip() == "docker-broker-ok"
    broker.terminate(context, environment, lease_token=lease.lease_token or "")
