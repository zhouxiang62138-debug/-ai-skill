"""F11.1 执行协议的数据模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.errors import RuntimeValidationError


_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeValidationError(f"{name} 不能为空")
    return value


def _require_hash(value: object, name: str) -> str:
    text = _require_text(value, name)
    if text == "unknown" or not _SHA256.fullmatch(text):
        raise RuntimeValidationError(f"{name} 必须是正式 SHA-256 值")
    return text


@dataclass(frozen=True)
class ExecutionContext:
    """传给 Hands 的最小结构化上下文，不包含 Store、Orchestrator 或 Token。"""

    session_id: str
    run_id: str
    worker_id: str
    lease_version: int
    role: str
    project_id: str
    project_root: str

    def __post_init__(self) -> None:
        for name in (
            "session_id",
            "run_id",
            "worker_id",
            "role",
            "project_id",
            "project_root",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.lease_version, int) or self.lease_version <= 0:
            raise RuntimeValidationError("lease_version 必须是正整数")


@dataclass(frozen=True)
class ExecutionProfile:
    """一次 Attempt 使用的代码和执行环境身份。"""

    name: str
    code_snapshot_hash: str
    environment_hash: str

    def __post_init__(self) -> None:
        _require_text(self.name, "execution_profile.name")
        _require_hash(self.code_snapshot_hash, "code_snapshot_hash")
        _require_hash(self.environment_hash, "environment_hash")


@dataclass(frozen=True)
class ExecutionRequest:
    """一次逻辑 Tool Invocation；argv 永远是参数数组。"""

    logical_call_id: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout: float = 60.0
    execution_profile: ExecutionProfile | Mapping[str, str] | str = "default"
    code_snapshot_hash: str | None = None
    environment_hash: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.logical_call_id, "logical_call_id")
        if isinstance(self.argv, str):
            raise RuntimeValidationError("argv 必须是参数数组，禁止 Shell command string")
        try:
            values = tuple(self.argv)
        except TypeError as exc:
            raise RuntimeValidationError("argv 必须是参数数组") from exc
        if not values or not all(isinstance(item, str) and item for item in values):
            raise RuntimeValidationError("argv 必须是非空字符串参数数组")
        object.__setattr__(self, "argv", values)
        _require_text(self.cwd, "cwd")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise RuntimeValidationError("timeout 必须是正数")
        if self.timeout <= 0:
            raise RuntimeValidationError("timeout 必须是正数")
        if self.code_snapshot_hash is not None:
            _require_hash(self.code_snapshot_hash, "code_snapshot_hash")
        if self.environment_hash is not None:
            _require_hash(self.environment_hash, "environment_hash")

    def resolved_profile(self) -> ExecutionProfile:
        """解析并校验 Attempt 所需的正式 hash。"""

        profile = self.execution_profile
        if isinstance(profile, ExecutionProfile):
            name = profile.name
            code_hash = profile.code_snapshot_hash
            environment_hash = profile.environment_hash
        elif isinstance(profile, Mapping):
            name = profile.get("name", "")
            code_hash = profile.get("code_snapshot_hash")
            environment_hash = profile.get("environment_hash")
        else:
            name = profile
            code_hash = None
            environment_hash = None
        return ExecutionProfile(
            name=_require_text(name, "execution_profile"),
            code_snapshot_hash=self.code_snapshot_hash or _require_hash(
                code_hash, "code_snapshot_hash"
            ),
            environment_hash=self.environment_hash or _require_hash(
                environment_hash, "environment_hash"
            ),
        )


@dataclass(frozen=True)
class ExecutionResult:
    """Hands 返回给 Broker 的执行事实；不含 Control Plane 引用。"""

    exit_code: int | None
    timed_out: bool
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False

    def __post_init__(self) -> None:
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise RuntimeValidationError("exit_code 必须是整数或 null")
        if not isinstance(self.timed_out, bool):
            raise RuntimeValidationError("timed_out 必须是布尔值")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise RuntimeValidationError("stdout/stderr 必须是字符串")
        if not isinstance(self.output_truncated, bool):
            raise RuntimeValidationError("output_truncated 必须是布尔值")

    def to_payload(
        self, *, logical_call_id: str, argv: tuple[str, ...], cwd: str
    ) -> dict[str, Any]:
        return {
            "logical_call_id": logical_call_id,
            "argv": list(argv),
            "cwd": cwd,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_truncated": self.output_truncated,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ExecutionResult":
        return cls(
            exit_code=payload.get("exit_code"),
            timed_out=bool(payload.get("timed_out", False)),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            output_truncated=bool(payload.get("output_truncated", False)),
        )


@dataclass(frozen=True)
class ExecutionReceipt:
    """Broker 对 Brain 暴露的可重放结果。"""

    logical_call_id: str
    tool_call_id: str
    attempt_id: str | None
    status: str
    result_reference: str
    result_hash: str
    result: ExecutionResult

