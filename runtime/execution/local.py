"""F11.2 Development/Compatibility 模式的本地执行后端。"""

from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, BinaryIO

from scripts.project_state import parse_project_yaml

from runtime.errors import RuntimeValidationError

from .base import ExecutionEnvironment
from .models import ExecutionContext, ExecutionRequest, ExecutionResult
from .path_policy import ExecutionPathPolicy
from .snapshots import WorkspaceSnapshotService


_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE_ENVIRONMENT = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|API[_-]?KEY|CREDENTIAL)"
)


_CLEANUP_ALREADY_EXITED = "ALREADY_EXITED"
_CLEANUP_TERMINATED = "TERMINATED"
_CLEANUP_FAILED = "CLEANUP_FAILED"


class _WindowsJobObject:
    """将一次 Windows invocation 与其后代进程绑定到同一个 Job。"""

    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    CLEANUP_WAIT_MILLISECONDS = 2000

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Object 仅在 Windows 可用")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle: ctypes.c_void_p | None = handle

    def _configure_api(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
        ]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.TerminateJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self._kernel32.TerminateJobObject.restype = ctypes.c_int
        self._kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self._kernel32.WaitForSingleObject.restype = ctypes.c_uint
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self._handle is None:
            raise OSError("Windows Job Object 已关闭")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise OSError("Windows Popen 缺少进程句柄")
        if not self._kernel32.AssignProcessToJobObject(
            self._handle,
            ctypes.c_void_p(int(process_handle)),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def _wait(self, milliseconds: int) -> int:
        if self._handle is None:
            return self.WAIT_OBJECT_0
        return int(self._kernel32.WaitForSingleObject(self._handle, milliseconds))

    def terminate(self, process: subprocess.Popen[bytes]) -> str:
        """在有限清理窗口内终止并等待 Job，返回确定的清理结果。"""

        if self._handle is None:
            return _CLEANUP_ALREADY_EXITED
        if self._wait(0) == self.WAIT_OBJECT_0:
            return _CLEANUP_ALREADY_EXITED
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            if self._wait(0) == self.WAIT_OBJECT_0:
                return _CLEANUP_ALREADY_EXITED
            return _CLEANUP_FAILED
        wait_result = self._wait(self.CLEANUP_WAIT_MILLISECONDS)
        if wait_result == self.WAIT_OBJECT_0:
            return _CLEANUP_TERMINATED
        if process.poll() is not None and self._wait(0) == self.WAIT_OBJECT_0:
            return _CLEANUP_TERMINATED
        return _CLEANUP_FAILED

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class _LocalConfig:
    output_limit_bytes: int
    stream_chunk_bytes: int
    max_timeout_seconds: float
    environment_allowlist: tuple[str, ...]
    profiles: dict[str, frozenset[str]]


class _BoundedCapture:
    """在线程中持续排空管道，只保留固定大小的前缀。"""

    def __init__(self, limit_bytes: int, chunk_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._chunk_bytes = chunk_bytes
        self._buffer = bytearray()
        self.truncated = False

    def consume(self, stream: BinaryIO) -> None:
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
            # 终止进程时父端关闭管道属于正常收尾路径。
            return

    def text(self) -> str:
        return bytes(self._buffer).decode("utf-8", errors="ignore")


def _load_config(path: str | Path | None) -> _LocalConfig:
    config_path = Path(path) if path else _ROOT / "config" / "execution.yaml"
    document = parse_project_yaml(config_path.read_text(encoding="utf-8"))
    section = document.get("local_compatibility")
    if not isinstance(section, dict):
        raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")

    output_limit = section.get("output_limit_bytes")
    chunk_bytes = section.get("stream_chunk_bytes")
    max_timeout = section.get("max_timeout_seconds")
    environment_allowlist = section.get("environment_allowlist")
    profiles = section.get("profiles")
    if (
        not isinstance(output_limit, int)
        or output_limit <= 0
        or not isinstance(chunk_bytes, int)
        or chunk_bytes <= 0
        or not isinstance(max_timeout, (int, float))
        or isinstance(max_timeout, bool)
        or max_timeout <= 0
        or not isinstance(environment_allowlist, list)
        or not all(isinstance(item, str) and item for item in environment_allowlist)
        or not isinstance(profiles, dict)
    ):
        raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")

    parsed_profiles: dict[str, frozenset[str]] = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not isinstance(profile, dict):
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")
        commands = profile.get("allowed_commands")
        if not isinstance(commands, list) or not all(
            isinstance(command, str) and command for command in commands
        ):
            raise RuntimeValidationError("EXECUTION_CONFIG_INVALID")
        parsed_profiles[name.casefold()] = frozenset(
            command.casefold() for command in commands
        )
    return _LocalConfig(
        output_limit_bytes=output_limit,
        stream_chunk_bytes=chunk_bytes,
        max_timeout_seconds=float(max_timeout),
        environment_allowlist=tuple(environment_allowlist),
        profiles=parsed_profiles,
    )


class LocalCompatibilityEnvironment(ExecutionEnvironment):
    """使用宿主权限的开发兼容后端，不提供安全 Sandbox。"""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        path_policy: ExecutionPathPolicy | None = None,
        snapshot_service: WorkspaceSnapshotService | None = None,
    ) -> None:
        self._config = _load_config(config_path)
        self._path_policy = path_policy or ExecutionPathPolicy()
        self._snapshot_service = snapshot_service
        self._root: Path | None = None
        self._active_process: subprocess.Popen[bytes] | None = None
        self._active_windows_job: _WindowsJobObject | None = None
        self._last_cleanup_status: str | None = None
        self._terminated = False

    @property
    def output_limit_bytes(self) -> int:
        """暴露确定性输出上限，供验收和调用方展示。"""

        return self._config.output_limit_bytes

    @property
    def last_cleanup_status(self) -> str | None:
        """返回最近一次进程树清理结果，供诊断与验收使用。"""

        return self._last_cleanup_status

    def _ensure_active(self, context: ExecutionContext) -> Path:
        if self._terminated:
            raise RuntimeValidationError("执行环境已终止")
        root = Path(context.project_root).resolve()
        if not root.is_dir():
            raise RuntimeValidationError("project_root 必须是已存在目录")
        if self._root is None:
            self._root = root
        elif self._root != root:
            raise RuntimeValidationError("执行环境不能切换 project_root")
        return root

    def provision(self, context: ExecutionContext) -> None:
        """绑定一个项目根目录；不接触 SessionStore 或 Lease。"""

        self._root = Path(context.project_root).resolve()
        if not self._root.is_dir():
            raise RuntimeValidationError("project_root 必须是已存在目录")
        self._terminated = False
        self._last_cleanup_status = None

    @staticmethod
    def _command_name(value: str) -> str:
        if os.name == "nt":
            return PureWindowsPath(value.replace("/", "\\")).name.casefold()
        return Path(value).name.casefold()

    def _assert_command_allowed(self, request: ExecutionRequest) -> None:
        profile = request.resolved_profile()
        allowed = self._config.profiles.get(profile.name.casefold())
        if not allowed:
            raise RuntimeValidationError("LOCAL_EXECUTION_PROFILE_NOT_ALLOWED")
        if self._command_name(request.argv[0]) not in allowed:
            raise RuntimeValidationError("LOCAL_COMMAND_NOT_ALLOWED")

    def _minimal_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        for name in self._config.environment_allowlist:
            if _SENSITIVE_ENVIRONMENT.search(name):
                continue
            value = os.environ.get(name)
            if value is not None:
                environment[name] = value
        return environment

    def _terminate_with_taskkill(
        self, process: subprocess.Popen[bytes]
    ) -> str:
        """Job Object 不可用时执行一次有界的 Windows fallback。"""

        if process.poll() is not None:
            return _CLEANUP_ALREADY_EXITED
        taskkill_succeeded = False
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._minimal_environment(),
                timeout=5,
            )
            taskkill_succeeded = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            taskkill_succeeded = False
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return _CLEANUP_FAILED
        return _CLEANUP_TERMINATED if taskkill_succeeded else _CLEANUP_FAILED

    def _terminate_process_tree(self, process: subprocess.Popen[bytes]) -> str:
        """终止一次 invocation，并等待可验证的进程树清理结果。"""

        if os.name == "nt":
            if self._active_windows_job is not None:
                return self._active_windows_job.terminate(process)
            return self._terminate_with_taskkill(process)
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.kill()
            except OSError:
                pass
        return _CLEANUP_TERMINATED if process.poll() is not None else _CLEANUP_FAILED

    def execute(
        self, context: ExecutionContext, request: ExecutionRequest
    ) -> ExecutionResult:
        """以 shell=False 执行已配置 Profile 允许的参数数组。"""

        self._ensure_active(context)
        cwd = self._path_policy.assert_cwd(context.project_root, request.cwd)
        self._assert_command_allowed(request)
        if request.timeout > self._config.max_timeout_seconds:
            raise RuntimeValidationError("LOCAL_TIMEOUT_EXCEEDS_LIMIT")
        self._last_cleanup_status = None

        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": self._minimal_environment(),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(list(request.argv), **popen_kwargs)
        self._active_process = process
        if os.name == "nt":
            try:
                self._active_windows_job = _WindowsJobObject()
                self._active_windows_job.assign(process)
            except (OSError, TypeError, ValueError):
                if self._active_windows_job is not None:
                    self._active_windows_job.close()
                self._active_windows_job = None
        stdout_capture = _BoundedCapture(
            self._config.output_limit_bytes, self._config.stream_chunk_bytes
        )
        stderr_capture = _BoundedCapture(
            self._config.output_limit_bytes, self._config.stream_chunk_bytes
        )
        stdout_thread = threading.Thread(
            target=stdout_capture.consume,
            args=(process.stdout,),
            name="local-stdout-capture",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.consume,
            args=(process.stderr,),
            name="local-stderr-capture",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        exit_code: int | None
        try:
            exit_code = process.wait(timeout=float(request.timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
            self._last_cleanup_status = self._terminate_process_tree(process)
        finally:
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            if self._active_windows_job is not None:
                self._active_windows_job.close()
                self._active_windows_job = None
            self._active_process = None

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
        return ExecutionResult(
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout_capture.text(),
            stderr=stderr_capture.text(),
            output_truncated=stdout_capture.truncated or stderr_capture.truncated,
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
                    context.role,
                    context.project_root,
                    relative,
                    operation="read",
                )
            except RuntimeValidationError:
                continue
            if resolved.is_file():
                files.append(relative)
        return sorted(files)

    def snapshot(self, context: ExecutionContext) -> str:
        self._ensure_active(context)
        if self._snapshot_service is None:
            raise RuntimeValidationError("SNAPSHOT_SERVICE_REQUIRED")
        return self._snapshot_service.create(context)

    def restore(self, context: ExecutionContext, snapshot_id: str) -> None:
        if self._snapshot_service is None:
            raise RuntimeValidationError("SNAPSHOT_SERVICE_REQUIRED")
        self._snapshot_service.restore(context, snapshot_id)
        self.provision(context)

    def terminate(self, context: ExecutionContext) -> None:
        self._ensure_active(context)
        if self._active_process is not None:
            self._last_cleanup_status = self._terminate_process_tree(
                self._active_process
            )
            if self._active_windows_job is not None:
                self._active_windows_job.close()
                self._active_windows_job = None
        self._terminated = True
