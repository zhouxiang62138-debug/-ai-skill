"""F11.3A 使用真实 Docker Container 的执行后端。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import threading
import uuid
from typing import Any, BinaryIO, Callable

from scripts.project_state import parse_project_yaml

from runtime.errors import RuntimeValidationError

from .base import ExecutionEnvironment
from .models import ExecutionContext, ExecutionRequest, ExecutionResult
from .path_policy import ExecutionPathPolicy


_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE_ENVIRONMENT = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")


@dataclass(frozen=True)
class _DockerConfig:
    image: str
    network: str
    privileged: bool
    host_pid: bool
    host_ipc: bool
    docker_socket: str
    no_new_privileges: bool
    cap_drop: tuple[str, ...]
    pids_limit: int
    memory: str
    cpus: float
    output_limit_bytes: int
    stream_chunk_bytes: int
    max_timeout_seconds: float
    hidden_paths: tuple[str, ...]
    protected_files: tuple[str, ...]
    mount_target: str
    mount_read_only: bool
    profiles: dict[str, frozenset[str]]
    keepalive_argv: tuple[str, ...]


@dataclass(frozen=True)
class _DockerCommandResult:
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    output_truncated: bool


class _BoundedCapture:
    """在线程中持续排空管道，并且只保留固定大小的前缀。"""

    def __init__(self, limit_bytes: int, chunk_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._chunk_bytes = chunk_bytes
        self._buffer = bytearray()
        self.truncated = False

    def consume(self, stream: BinaryIO | None) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(self._chunk_bytes)
                if not chunk:
                    return
                if not isinstance(chunk, bytes):
                    chunk = str(chunk).encode("utf-8", errors="replace")
                remaining = self._limit_bytes - len(self._buffer)
                if remaining > 0:
                    self._buffer.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    self.truncated = True
        except (OSError, ValueError):
            # 终止 Docker CLI 时，父进程关闭管道属于正常收尾路径。
            return

    def text(self) -> str:
        return bytes(self._buffer).decode("utf-8", errors="ignore")


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeValidationError(f"{name} 不能为空")
    return value


def _load_config(path: str | Path | None) -> _DockerConfig:
    config_path = Path(path) if path else _ROOT / "config" / "execution.yaml"
    try:
        document = parse_project_yaml(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeValidationError("EXECUTION_CONFIG_INVALID") from exc
    section = document.get("docker")
    if not isinstance(section, dict):
        raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")

    image = section.get("image")
    network = section.get("network")
    privileged = section.get("privileged")
    host_pid = section.get("host_pid")
    host_ipc = section.get("host_ipc")
    docker_socket = section.get("docker_socket")
    no_new_privileges = section.get("no_new_privileges")
    cap_drop = section.get("cap_drop")
    pids_limit = section.get("pids_limit")
    memory = section.get("memory")
    cpus = section.get("cpus")
    output_limit = section.get("output_limit_bytes")
    chunk_bytes = section.get("stream_chunk_bytes")
    max_timeout = section.get("max_timeout_seconds")
    hidden_paths = section.get("hidden_paths")
    protected_files = section.get("protected_files")
    mount = section.get("mount")
    profiles = section.get("profiles")
    keepalive_argv = section.get("keepalive_argv")

    if (
        not isinstance(image, str)
        or not image
        or network != "none"
        or privileged is not False
        or host_pid is not False
        or host_ipc is not False
        or docker_socket != "forbidden"
        or no_new_privileges is not True
        or not isinstance(cap_drop, list)
        or "ALL" not in cap_drop
        or not isinstance(pids_limit, int)
        or pids_limit <= 0
        or not isinstance(memory, str)
        or not memory
        or not isinstance(cpus, (int, float))
        or isinstance(cpus, bool)
        or cpus <= 0
        or not isinstance(output_limit, int)
        or output_limit <= 0
        or not isinstance(chunk_bytes, int)
        or chunk_bytes <= 0
        or not isinstance(max_timeout, (int, float))
        or isinstance(max_timeout, bool)
        or max_timeout <= 0
        or not isinstance(hidden_paths, list)
        or not all(isinstance(item, str) and item for item in hidden_paths)
        or not isinstance(protected_files, list)
        or not all(isinstance(item, str) and item for item in protected_files)
        or not isinstance(mount, dict)
        or not isinstance(mount.get("target"), str)
        or not mount["target"].startswith("/")
        or not isinstance(mount.get("read_only"), bool)
        or not isinstance(profiles, dict)
        or not isinstance(keepalive_argv, list)
        or not keepalive_argv
        or not all(isinstance(item, str) and item for item in keepalive_argv)
    ):
        raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")

    for hidden in hidden_paths:
        normalized = hidden.replace("\\", "/").strip("/")
        if not normalized or ".." in Path(normalized).parts:
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")
    for protected in protected_files:
        normalized = protected.replace("\\", "/").strip("/")
        if not normalized or "/" in normalized or ".." in Path(normalized).parts:
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")

    parsed_profiles: dict[str, frozenset[str]] = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name or not isinstance(profile, dict):
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")
        commands = profile.get("allowed_commands")
        if not isinstance(commands, list) or not all(
            isinstance(command, str) and command for command in commands
        ):
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")
        parsed_profiles[name.casefold()] = frozenset(
            command.casefold() for command in commands
        )

    return _DockerConfig(
        image=image,
        network=network,
        privileged=privileged,
        host_pid=host_pid,
        host_ipc=host_ipc,
        docker_socket=docker_socket,
        no_new_privileges=no_new_privileges,
        cap_drop=tuple(cap_drop),
        pids_limit=pids_limit,
        memory=memory,
        cpus=float(cpus),
        output_limit_bytes=output_limit,
        stream_chunk_bytes=chunk_bytes,
        max_timeout_seconds=float(max_timeout),
        hidden_paths=tuple(hidden_paths),
        protected_files=tuple(protected_files),
        mount_target=mount["target"].rstrip("/") or "/",
        mount_read_only=mount["read_only"],
        profiles=parsed_profiles,
        keepalive_argv=tuple(keepalive_argv),
    )


class DockerExecutionEnvironment(ExecutionEnvironment):
    """通过 Docker CLI 管理真实 Container 的最小执行后端。"""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        path_policy: ExecutionPathPolicy | None = None,
    ) -> None:
        self._config = _load_config(config_path)
        self._path_policy = path_policy or ExecutionPathPolicy()
        self._docker_binary = shutil.which("docker") or "docker"
        self._root: Path | None = None
        self._container_id: str | None = None
        self._image_digest: str | None = None
        self._terminated = False

    @property
    def container_id(self) -> str | None:
        return self._container_id

    @property
    def image(self) -> str:
        return self._config.image

    @property
    def output_limit_bytes(self) -> int:
        return self._config.output_limit_bytes

    @property
    def environment_hash(self) -> str:
        """返回默认 Docker profile 对应的真实环境身份 hash。"""

        profile_name = next(iter(self._config.profiles))
        return self.environment_hash_for(profile_name)

    def _docker_environment(self) -> dict[str, str]:
        """Docker CLI 只接收运行 CLI 所需的系统变量，不继承用户环境。"""

        if os.name != "nt":
            return {}
        environment: dict[str, str] = {}
        for name in ("SYSTEMROOT", "WINDIR"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        return environment

    def _run_docker(
        self,
        arguments: list[str],
        *,
        timeout: float,
        allow_failure: bool = False,
        on_timeout: Callable[[], None] | None = None,
    ) -> _DockerCommandResult:
        command = [self._docker_binary, *arguments]
        try:
            process = subprocess.Popen(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._docker_environment(),
            )
        except OSError as exc:
            raise RuntimeValidationError("DOCKER_UNAVAILABLE") from exc

        stdout_capture = _BoundedCapture(
            self._config.output_limit_bytes, self._config.stream_chunk_bytes
        )
        stderr_capture = _BoundedCapture(
            self._config.output_limit_bytes, self._config.stream_chunk_bytes
        )
        stdout_thread = threading.Thread(
            target=stdout_capture.consume,
            args=(process.stdout,),
            name="docker-stdout-capture",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.consume,
            args=(process.stderr,),
            name="docker-stderr-capture",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if on_timeout is not None:
                try:
                    on_timeout()
                except RuntimeValidationError:
                    pass
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            returncode = None
        finally:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        result = _DockerCommandResult(
            returncode=returncode,
            timed_out=timed_out,
            stdout=stdout_capture.text(),
            stderr=stderr_capture.text(),
            output_truncated=stdout_capture.truncated or stderr_capture.truncated,
        )
        if not allow_failure and (result.timed_out or result.returncode != 0):
            raise RuntimeValidationError("DOCKER_COMMAND_FAILED")
        return result

    def _ensure_image_digest(self) -> str:
        if self._image_digest is not None:
            return self._image_digest
        result = self._run_docker(
            ["image", "inspect", "--format={{.Id}}", self._config.image],
            timeout=30,
        )
        digest = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if not digest.startswith("sha256:"):
            raise RuntimeValidationError("DOCKER_IMAGE_DIGEST_UNAVAILABLE")
        self._image_digest = digest
        return digest

    def environment_hash_for(self, profile_name: str) -> str:
        """根据镜像身份和实际 Docker 安全策略计算 SHA-256 环境身份。"""

        name = _require_text(profile_name, "execution_profile")
        if name.casefold() not in self._config.profiles:
            raise RuntimeValidationError("DOCKER_EXECUTION_PROFILE_NOT_ALLOWED")
        identity = {
            "backend": "docker",
            "image": self._config.image,
            "image_digest": self._ensure_image_digest(),
            "execution_profile": name,
            "network": self._config.network,
            "resource_policy": {
                "pids_limit": self._config.pids_limit,
                "memory": self._config.memory,
                "cpus": self._config.cpus,
            },
            "container_policy": {
                "privileged": self._config.privileged,
                "host_pid": self._config.host_pid,
                "host_ipc": self._config.host_ipc,
                "docker_socket": self._config.docker_socket,
                "no_new_privileges": self._config.no_new_privileges,
                "cap_drop": self._config.cap_drop,
            },
            "mount_policy": {
                "source": "current_execution_workspace",
                "target": self._config.mount_target,
                "read_only": self._config.mount_read_only,
                "hidden_paths": self._config.hidden_paths,
                "protected_files": self._config.protected_files,
            },
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _ensure_active(self, context: ExecutionContext) -> Path:
        if self._terminated:
            raise RuntimeValidationError("执行环境已终止")
        root = Path(context.project_root).resolve()
        if not root.is_dir():
            raise RuntimeValidationError("project_root 必须是已存在的目录")
        if self._root is None:
            self._root = root
        elif self._root != root:
            raise RuntimeValidationError("执行环境不能切换 project_root")
        if self._container_id is None:
            raise RuntimeValidationError("Docker Container 尚未 provision")
        return root

    @staticmethod
    def _command_name(value: str) -> str:
        if os.name == "nt":
            return PureWindowsPath(value.replace("/", "\\")).name.casefold()
        return Path(value).name.casefold()

    def _assert_request_allowed(self, request: ExecutionRequest) -> None:
        profile = request.resolved_profile()
        allowed = self._config.profiles.get(profile.name.casefold())
        if not allowed:
            raise RuntimeValidationError("DOCKER_EXECUTION_PROFILE_NOT_ALLOWED")
        if self._command_name(request.argv[0]) not in allowed:
            raise RuntimeValidationError("DOCKER_COMMAND_NOT_ALLOWED")
        if profile.environment_hash != self.environment_hash_for(profile.name):
            raise RuntimeValidationError("DOCKER_ENVIRONMENT_HASH_MISMATCH")

    def _container_cwd(self, root: Path, cwd: str) -> str:
        host_cwd = self._path_policy.assert_cwd(str(root), cwd)
        relative = host_cwd.relative_to(root).as_posix()
        if relative == ".":
            return self._config.mount_target
        return f"{self._config.mount_target}/{relative}"

    def provision(self, context: ExecutionContext) -> None:
        """只挂载当前项目 workspace，并用 tmpfs 隐藏项目内控制面目录。"""

        root = Path(context.project_root).resolve()
        if not root.is_dir():
            raise RuntimeValidationError("project_root 必须是已存在的目录")
        if self._container_id is not None:
            self.terminate(context)
        self._root = root
        self._terminated = False
        self._ensure_image_digest()

        name = f"ai-dev-exec-{uuid.uuid4().hex}"
        mount = (
            f"type=bind,source={root},target={self._config.mount_target}"
        )
        if self._config.mount_read_only:
            mount += ",readonly"
        arguments = [
            "create",
            "--name",
            name,
            "--network",
            self._config.network,
            "--privileged=false",
            "--pid=private",
            "--ipc=private",
            "--security-opt",
            "no-new-privileges:true",
        ]
        for capability in self._config.cap_drop:
            arguments.extend(["--cap-drop", capability])
        arguments.extend(
            [
                "--pids-limit",
                str(self._config.pids_limit),
                "--memory",
                self._config.memory,
                "--cpus",
                str(self._config.cpus),
                "--mount",
                mount,
            ]
        )
        for hidden in self._config.hidden_paths:
            arguments.extend(["--tmpfs", f"{self._config.mount_target}/{hidden}"])
        for protected in self._config.protected_files:
            source = root / protected
            if source.is_file():
                arguments.extend(
                    [
                        "--mount",
                        f"type=bind,source={source},target={self._config.mount_target}/{protected},readonly",
                    ]
                )
        arguments.extend([self._config.image, *self._config.keepalive_argv])

        created = self._run_docker(arguments, timeout=60)
        container_id = created.stdout.strip().splitlines()[0] if created.stdout.strip() else ""
        if not container_id:
            raise RuntimeValidationError("DOCKER_CONTAINER_CREATE_FAILED")
        self._container_id = container_id
        try:
            self._run_docker(["start", container_id], timeout=60)
        except Exception:
            self._run_docker(["rm", "-f", container_id], timeout=30, allow_failure=True)
            self._container_id = None
            raise

    def execute(
        self, context: ExecutionContext, request: ExecutionRequest
    ) -> ExecutionResult:
        """通过 docker exec 在 Container 内执行参数数组。"""

        root = self._ensure_active(context)
        container_cwd = self._container_cwd(root, request.cwd)
        if request.timeout > self._config.max_timeout_seconds:
            raise RuntimeValidationError("DOCKER_TIMEOUT_EXCEEDS_LIMIT")
        self._assert_request_allowed(request)
        assert self._container_id is not None
        result = self._run_docker(
            [
                "exec",
                "--workdir",
                container_cwd,
                self._container_id,
                *request.argv,
            ],
            timeout=float(request.timeout),
            allow_failure=True,
            on_timeout=self._kill_container,
        )
        return ExecutionResult(
            exit_code=result.returncode,
            timed_out=result.timed_out,
            stdout=result.stdout,
            stderr=result.stderr,
            output_truncated=result.output_truncated,
        )

    def read_file(self, context: ExecutionContext, path: str) -> str:
        self._ensure_active(context)
        target = self._path_policy.assert_path(
            context.role, context.project_root, path, operation="read"
        )
        return target.read_text(encoding="utf-8")

    def write_file(self, context: ExecutionContext, path: str, content: str) -> None:
        self._ensure_active(context)
        if not isinstance(content, str):
            raise RuntimeValidationError("文件内容必须是字符串")
        target = self._path_policy.assert_path(
            context.role, context.project_root, path, operation="write"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_files(self, context: ExecutionContext, path: str = ".") -> list[str]:
        root = self._ensure_active(context)
        directory = self._path_policy.assert_path(
            context.role, context.project_root, path, operation="read"
        )
        if not directory.is_dir():
            raise RuntimeValidationError("list_files 目标必须是目录")
        files: list[str] = []
        for item in directory.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(root).as_posix()
            try:
                resolved = self._path_policy.assert_path(
                    context.role, context.project_root, relative, operation="read"
                )
            except RuntimeValidationError:
                continue
            if resolved.is_file():
                files.append(relative)
        return sorted(files)

    def snapshot(self, context: ExecutionContext) -> str:
        self._ensure_active(context)
        raise RuntimeValidationError("SNAPSHOT_NOT_IMPLEMENTED")

    def restore(self, context: ExecutionContext, snapshot_id: str) -> None:
        self._ensure_active(context)
        raise RuntimeValidationError("RESTORE_NOT_IMPLEMENTED")

    def _kill_container(self) -> None:
        if self._container_id is None:
            return
        self._run_docker(
            ["kill", self._container_id], timeout=30, allow_failure=True
        )

    def terminate(self, context: ExecutionContext) -> None:
        if self._terminated:
            return
        self._ensure_active(context)
        assert self._container_id is not None
        container_id = self._container_id
        killed = self._run_docker(["kill", container_id], timeout=30, allow_failure=True)
        if killed.returncode not in (0, None):
            self._run_docker(["stop", container_id], timeout=30, allow_failure=True)
        self._run_docker(["rm", "-f", container_id], timeout=30)
        self._container_id = None
        self._terminated = True
