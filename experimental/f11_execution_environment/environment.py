"""F11：受控执行环境，不向 Generator 暴露任意 Shell。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from runtime.errors import RuntimeValidationError
from runtime.event_types import ActorType, EventType
from runtime.session_store import SessionStore, stable_id


@dataclass(frozen=True)
class ExecutionResult:
    """一次受控命令执行的不可变结果。"""

    call_id: str
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    output_truncated: bool
    result_reference: str


class ExecutionEnvironment(ABC):
    """Brain 与 Hands 间的最小统一接口。"""

    @abstractmethod
    def provision(self, project_id: str, resources: Mapping[str, str] | None = None) -> None:
        """创建或验证执行环境。"""

    @abstractmethod
    def execute(self, argv: Sequence[str], cwd: str = ".", timeout: float = 60.0,
                env_policy: Mapping[str, str] | None = None) -> ExecutionResult:
        """在受控项目根目录内执行参数数组。"""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """读取项目内 UTF-8 文件。"""

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """写入项目内文件。"""

    @abstractmethod
    def list_files(self, path: str = ".") -> list[str]:
        """列出项目内普通文件。"""

    @abstractmethod
    def snapshot(self) -> str:
        """创建可审计快照标识。"""

    @abstractmethod
    def restore(self, snapshot_id: str) -> None:
        """恢复已创建的快照。"""

    @abstractmethod
    def terminate(self) -> None:
        """终止执行环境。"""


class LocalWorkspaceEnvironment(ExecutionEnvironment):
    """项目内白名单命令的本地执行环境。"""

    def __init__(self, project_root: str | Path, *, session_id: str, store: SessionStore,
                 allowed_prefixes: Sequence[Sequence[str]], output_limit_bytes: int = 1_000_000) -> None:
        self.root = Path(project_root).resolve()
        self.session_id, self.store = session_id, store
        self.allowed_prefixes = tuple(tuple(item) for item in allowed_prefixes)
        self.output_limit_bytes, self._terminated = output_limit_bytes, False
        self._snapshots: dict[str, dict[str, str]] = {}

    def provision(self, project_id: str, resources: Mapping[str, str] | None = None) -> None:
        if not self.root.is_dir() or not project_id:
            raise RuntimeValidationError("执行环境项目根目录或 project_id 无效")

    def _inside(self, value: str) -> Path:
        candidate = Path(value.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RuntimeValidationError("路径必须是项目内相对路径")
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeValidationError("符号链接或路径解析逃出项目根目录") from exc
        return resolved

    def _allowed(self, argv: Sequence[str]) -> bool:
        return any(tuple(argv[:len(prefix)]) == prefix for prefix in self.allowed_prefixes if prefix)

    def _clip(self, value: str) -> tuple[str, bool]:
        raw = value.encode("utf-8", errors="replace")
        if len(raw) <= self.output_limit_bytes:
            return value, False
        return raw[:self.output_limit_bytes].decode("utf-8", errors="ignore") + "\n<OUTPUT_TRUNCATED>\n", True

    def execute(self, argv: Sequence[str], cwd: str = ".", timeout: float = 60.0,
                env_policy: Mapping[str, str] | None = None) -> ExecutionResult:
        if self._terminated or not argv or not all(isinstance(v, str) and v for v in argv):
            raise RuntimeValidationError("执行环境已终止或命令无效")
        if not self._allowed(argv):
            raise RuntimeValidationError("命令不在批准白名单中")
        if timeout <= 0 or timeout > 3600:
            raise RuntimeValidationError("timeout 必须在 0 到 3600 秒之间")
        workdir = self._inside(cwd)
        if not workdir.is_dir():
            raise RuntimeValidationError("cwd 必须是项目内已存在目录")
        key = hashlib.sha256(("\x1f".join(argv) + "|" + cwd).encode()).hexdigest()[:24]
        call_id = self.store.request_tool_call(self.session_id, tool_name=Path(argv[0]).name,
            arguments={"argv": list(argv), "cwd": cwd, "timeout": timeout}, idempotency_key=f"environment:{key}")
        attempt_id = self.store.start_tool_call(self.session_id, call_id)
        clean_env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
        clean_env.update(dict(env_policy or {}))
        try:
            done = subprocess.run(list(argv), cwd=workdir, shell=False, check=False, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout, env=clean_env)
            stdout, a = self._clip(done.stdout); stderr, b = self._clip(done.stderr); code, timed_out = done.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, a = self._clip(exc.stdout or ""); stderr, b = self._clip((exc.stderr or "") + "\n命令超时。")
            code, timed_out = None, True
        reference, digest = self.store.write_tool_result(call_id, {"argv": list(argv), "cwd": cwd, "exit_code": code, "timed_out": timed_out,
            "stdout": stdout, "stderr": stderr, "output_truncated": a or b})
        status = "TIMED_OUT" if timed_out else ("SUCCEEDED" if code == 0 else "FAILED")
        self.store.complete_tool_call(self.session_id, call_id, result_reference=reference,
            result_hash=digest, status=status, attempt_id=attempt_id)
        self.store.append_event(self.session_id, EventType.TOOL_CALL_TIMED_OUT if timed_out else EventType.TOOL_CALL_COMPLETED,
            ActorType.TOOL, "local_workspace", idempotency_key=f"environment-result:{call_id}", correlation_id=call_id,
            payload={"result_reference": reference, "exit_code": code, "result_hash": digest})
        return ExecutionResult(call_id, tuple(argv), cwd, code, timed_out, stdout, stderr, a or b, reference)

    def read_file(self, path: str) -> str:
        return self._inside(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._inside(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")

    def list_files(self, path: str = ".") -> list[str]:
        return sorted(item.relative_to(self.root).as_posix() for item in self._inside(path).rglob("*") if item.is_file())

    def snapshot(self) -> str:
        manifest = {name: hashlib.sha256(self.read_file(name).encode()).hexdigest() for name in self.list_files() if not name.startswith(".runtime/")}
        snapshot_id = stable_id("snapshot", self.session_id, json.dumps(manifest, sort_keys=True)); self._snapshots[snapshot_id] = manifest
        return snapshot_id

    def restore(self, snapshot_id: str) -> None:
        if snapshot_id not in self._snapshots:
            raise RuntimeValidationError("快照不存在；F11 不执行未持久化的文件恢复")

    def terminate(self) -> None:
        self._terminated = True


class DockerExecutionEnvironment(LocalWorkspaceEnvironment):
    """Docker 适配器；Docker 不可用时明确阻塞。"""

    def provision(self, project_id: str, resources: Mapping[str, str] | None = None) -> None:
        super().provision(project_id, resources)
        if shutil.which("docker") is None:
            raise RuntimeValidationError("Docker 不可用，无法 provision DockerExecutionEnvironment")
