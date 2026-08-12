"""Core Role Run 的隔离执行协议。

这个模块只描述 Runtime 与宿主之间的边界，不把任何 Codex SDK 或聊天 API
写死在 Runtime 内。真实 Child Thread 只有在宿主能力声明、返回稳定 ID 并且
能够执行该线程时才会被记录；否则统一降级为独立 Fresh Invocation。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError
from .session_store import SessionStore


CORE_ROLES = frozenset({"planner", "generator", "evaluator"})


class ExecutionMode(StrEnum):
    CHILD_THREAD = "CHILD_THREAD"
    FRESH_INVOCATION = "FRESH_INVOCATION"


class WorkspaceMode(StrEnum):
    SHARED = "SHARED"
    WORKTREE = "WORKTREE"
    SELECTABLE = "SELECTABLE"


@dataclass(frozen=True)
class HostCapabilityProfile:
    """宿主真实能力快照；不从 Prompt 推断。"""

    child_thread_supported: bool = False
    stable_thread_id: bool = False
    programmatic_spawn: bool = False
    resumable: bool = False
    fresh_invocation_supported: bool = True
    workspace_mode: WorkspaceMode = WorkspaceMode.SHARED
    host_name: str = "unavailable"

    def __post_init__(self) -> None:
        for name in (
            "child_thread_supported",
            "stable_thread_id",
            "programmatic_spawn",
            "resumable",
            "fresh_invocation_supported",
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeValidationError("HOST_CAPABILITY_PROFILE_INVALID")
        if not isinstance(self.workspace_mode, WorkspaceMode):
            try:
                object.__setattr__(self, "workspace_mode", WorkspaceMode(self.workspace_mode))
            except (TypeError, ValueError) as exc:
                raise RuntimeValidationError("HOST_CAPABILITY_PROFILE_INVALID") from exc
        if not isinstance(self.host_name, str) or not self.host_name:
            raise RuntimeValidationError("HOST_CAPABILITY_PROFILE_INVALID")

    @property
    def real_child_thread_ready(self) -> bool:
        return (
            self.child_thread_supported
            and self.stable_thread_id
            and self.programmatic_spawn
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_thread": {
                "supported": self.child_thread_supported,
                "stable_thread_id": self.stable_thread_id,
                "programmatic_spawn": self.programmatic_spawn,
                "resumable": self.resumable,
            },
            "fresh_invocation": {"supported": self.fresh_invocation_supported},
            "workspace": {"mode": self.workspace_mode.value.lower()},
            "host_name": self.host_name,
        }


@dataclass(frozen=True)
class WorkspaceBinding:
    """代码执行空间与权威 project.yaml 提交路径的绑定。"""

    mode: WorkspaceMode
    project_root: str
    source_revision: int
    authoritative_state: str = "RUNTIME_CAS"
    project_state_write: str = "RUNTIME_CAS_ONLY"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, WorkspaceMode):
            try:
                object.__setattr__(self, "mode", WorkspaceMode(self.mode))
            except (TypeError, ValueError) as exc:
                raise RuntimeValidationError("WORKSPACE_BINDING_INVALID") from exc
        if not isinstance(self.project_root, str) or not self.project_root:
            raise RuntimeValidationError("WORKSPACE_BINDING_INVALID")
        if not isinstance(self.source_revision, int) or self.source_revision < 0:
            raise RuntimeValidationError("WORKSPACE_BINDING_INVALID")
        if self.authoritative_state != "RUNTIME_CAS":
            raise RuntimeValidationError("WORKSPACE_AUTHORITY_INVALID")
        if self.project_state_write != "RUNTIME_CAS_ONLY":
            raise RuntimeValidationError("WORKSPACE_STATE_WRITE_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "project_root": self.project_root,
            "source_revision": self.source_revision,
            "authoritative_state": self.authoritative_state,
            "project_state_write": self.project_state_write,
        }


@dataclass(frozen=True)
class RoleExecutionRequest:
    """Orchestrator 发给 Role Execution Broker 的最小请求。"""

    session_id: str
    run_id: str
    role: str
    project_id: str
    project_root: str
    source_revision: int
    context_manifest_id: str
    workspace_binding: WorkspaceBinding
    preferred_mode: ExecutionMode = ExecutionMode.CHILD_THREAD
    fallback_mode: ExecutionMode = ExecutionMode.FRESH_INVOCATION
    main_thread_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "session_id",
            "run_id",
            "role",
            "project_id",
            "project_root",
            "context_manifest_id",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise RuntimeValidationError("ROLE_EXECUTION_REQUEST_INVALID")
        if self.role not in CORE_ROLES:
            raise RuntimeValidationError("RUNTIME_ROLE_NOT_ALLOWED")
        if not isinstance(self.source_revision, int) or self.source_revision < 0:
            raise RuntimeValidationError("ROLE_EXECUTION_REQUEST_INVALID")
        try:
            object.__setattr__(self, "preferred_mode", ExecutionMode(self.preferred_mode))
            object.__setattr__(self, "fallback_mode", ExecutionMode(self.fallback_mode))
        except (TypeError, ValueError) as exc:
            raise RuntimeValidationError("ROLE_EXECUTION_MODE_INVALID") from exc
        if self.main_thread_id is not None and not isinstance(self.main_thread_id, str):
            raise RuntimeValidationError("ROLE_EXECUTION_REQUEST_INVALID")
        if Path(self.workspace_binding.project_root).resolve() != Path(self.project_root).resolve() or self.workspace_binding.source_revision != self.source_revision:
            raise RuntimeValidationError("WORKSPACE_BINDING_REVISION_MISMATCH")


class RoleExecutionHost(Protocol):
    """真实宿主适配器；实现者可以连接 Codex App Server 或其他 Host。"""

    def capabilities(self) -> HostCapabilityProfile:
        ...

    def create_child_thread(self, request: RoleExecutionRequest) -> str:
        ...

    def invoke_child(self, thread_id: str, request: Any) -> Mapping[str, Any]:
        ...

    def terminate_child(self, thread_id: str, *, reason: str) -> None:
        ...

    def resume_child(self, thread_id: str, request: RoleExecutionRequest) -> bool:
        ...


class UnavailableRoleExecutionHost:
    """默认宿主：明确表示没有 Child Thread API，不制造假线程。"""

    def capabilities(self) -> HostCapabilityProfile:
        return HostCapabilityProfile()


@dataclass(frozen=True)
class RoleExecutionPolicy:
    default_mode: ExecutionMode
    fallback_mode: ExecutionMode
    fresh_execution_per_role_run: bool
    reuse_completed_role_thread: bool
    preferred_modes: Mapping[str, ExecutionMode]
    evaluator_fallback_requires_fresh: bool
    workspace_mode: WorkspaceMode

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "RoleExecutionPolicy":
        root = Path(__file__).resolve().parents[1]
        path = Path(config_path) if config_path else root / "config" / "role_execution.yaml"
        try:
            document = parse_project_yaml(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_UNAVAILABLE") from exc
        section = document.get("role_execution")
        if document.get("version") != 1 or not isinstance(section, dict):
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID")

        def mode(value: Any, field: str) -> ExecutionMode:
            try:
                return ExecutionMode(str(value).upper())
            except ValueError as exc:
                raise RuntimeValidationError(f"ROLE_EXECUTION_POLICY_INVALID:{field}") from exc

        preferred: dict[str, ExecutionMode] = {}
        for role in CORE_ROLES:
            raw = section.get(role)
            if not isinstance(raw, dict):
                raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID")
            preferred[role] = mode(raw.get("preferred_mode"), f"{role}.preferred_mode")
        evaluator = section.get("evaluator")
        if not isinstance(evaluator, dict):
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID")
        workspace = section.get("workspace")
        if not isinstance(workspace, dict):
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID")
        try:
            workspace_mode = WorkspaceMode(str(workspace.get("preferred_mode")).upper())
        except ValueError as exc:
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID:workspace") from exc
        if workspace.get("authoritative_state") != "runtime_cas" or workspace.get("project_state_write") != "runtime_cas_only":
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID:workspace_authority")
        if section.get("fresh_execution_per_role_run") is not True or section.get("reuse_completed_role_thread") is not False:
            raise RuntimeValidationError("ROLE_EXECUTION_POLICY_INVALID:freshness")
        return cls(
            default_mode=mode(section.get("default_mode"), "default_mode"),
            fallback_mode=mode(section.get("fallback_mode"), "fallback_mode"),
            fresh_execution_per_role_run=True,
            reuse_completed_role_thread=False,
            preferred_modes=preferred,
            evaluator_fallback_requires_fresh=evaluator.get("fallback_requires_fresh_invocation") is True,
            workspace_mode=workspace_mode,
        )

    def preferred_for(self, role: str) -> ExecutionMode:
        if role not in self.preferred_modes:
            raise RuntimeValidationError("RUNTIME_ROLE_NOT_ALLOWED")
        return self.preferred_modes[role]


class RoleExecutionBroker:
    """把 Role Run 绑定到真实 Child Thread 或独立 Fresh Invocation。"""

    def __init__(
        self,
        store: SessionStore,
        *,
        host: RoleExecutionHost | None = None,
        policy: RoleExecutionPolicy | None = None,
    ) -> None:
        self.store = store
        self.host: RoleExecutionHost = host or UnavailableRoleExecutionHost()  # type: ignore[assignment]
        self.policy = policy or RoleExecutionPolicy.load()

    def capabilities(self) -> HostCapabilityProfile:
        profile = self.host.capabilities()
        if not isinstance(profile, HostCapabilityProfile):
            raise RuntimeValidationError("HOST_CAPABILITY_PROFILE_INVALID")
        return profile

    @staticmethod
    def _workspace_payload(binding: WorkspaceBinding) -> dict[str, Any]:
        return binding.to_dict()

    def _choose_mode(
        self, request: RoleExecutionRequest
    ) -> tuple[ExecutionMode, str | None, HostCapabilityProfile]:
        profile = self.capabilities()
        preferred = request.preferred_mode
        if profile.workspace_mode == WorkspaceMode.WORKTREE:
            # 当前 Runtime 没有实现 worktree 与权威 project.yaml 的同步协议。
            preferred = request.fallback_mode
            reason = "HOST_WORKTREE_STATE_DIVERGENCE_UNSAFE"
        elif preferred == ExecutionMode.CHILD_THREAD and profile.real_child_thread_ready:
            return preferred, None, profile
        else:
            reason = "HOST_CHILD_THREAD_UNAVAILABLE"
            preferred = request.fallback_mode
        if preferred != ExecutionMode.FRESH_INVOCATION or request.fallback_mode != ExecutionMode.FRESH_INVOCATION:
            raise RuntimeValidationError("ROLE_EXECUTION_FALLBACK_INVALID")
        if not profile.fresh_invocation_supported:
            raise RuntimeValidationError("ROLE_EXECUTION_HOST_UNAVAILABLE")
        return ExecutionMode.FRESH_INVOCATION, reason, profile

    def start_role_execution(self, request: RoleExecutionRequest, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """创建并启动一次新的 Role Execution；每次 Role Run 默认不复用旧线程。"""

        session = self.store.get_session(request.session_id)
        if session.project_id != request.project_id or Path(session.project_root).resolve() != Path(request.project_root).resolve():
            raise RuntimeValidationError("ROLE_EXECUTION_PROJECT_BINDING_MISMATCH")
        run = self.store.get_role_run(request.session_id, request.run_id)
        if run["role"] != request.role or run["status"] != "STARTED":
            raise RuntimeValidationError("ROLE_EXECUTION_ROLE_RUN_INVALID")
        context = self.store.get_context_manifest(request.session_id, request.context_manifest_id)
        if context["run_id"] != request.run_id or context["role"] != request.role or int(context["project_revision"]) != request.source_revision:
            raise RuntimeValidationError("ROLE_EXECUTION_CONTEXT_BINDING_MISMATCH")
        mode, fallback_reason, profile = self._choose_mode(request)
        host_thread_id: str | None = None
        execution_key = idempotency_key or f"role-execution:{request.run_id}:{request.context_manifest_id}"
        if mode == ExecutionMode.CHILD_THREAD:
            execution = self.store.create_role_execution(
                request.session_id,
                request.run_id,
                request.role,
                project_id=request.project_id,
                source_revision=request.source_revision,
                context_manifest_id=request.context_manifest_id,
                execution_mode=ExecutionMode.CHILD_THREAD.value,
                requested_mode=request.preferred_mode.value,
                host_thread_id=None,
                workspace_binding=self._workspace_payload(request.workspace_binding),
                fallback_reason=None,
                idempotency_key=execution_key,
                status="REQUESTED",
            )
            if execution["status"] == "STARTED":
                execution["capabilities"] = profile.to_dict()
                return execution
            try:
                creator = getattr(self.host, "create_child_thread", None)
                if not callable(creator):
                    raise RuntimeValidationError("HOST_CHILD_THREAD_SPAWN_UNAVAILABLE")
                value = creator(request)
                if not isinstance(value, str) or not value or value == request.main_thread_id:
                    raise RuntimeValidationError("HOST_CHILD_THREAD_ID_INVALID")
                host_thread_id = value
            except Exception as exc:
                if request.fallback_mode != ExecutionMode.FRESH_INVOCATION or not profile.fresh_invocation_supported:
                    raise RuntimeValidationError("ROLE_EXECUTION_HOST_UNAVAILABLE") from exc
                mode = ExecutionMode.FRESH_INVOCATION
                fallback_reason = "HOST_CHILD_THREAD_SPAWN_FAILED"
            execution = self.store.activate_role_execution(
                request.session_id,
                str(execution["role_execution_id"]),
                execution_mode=mode.value,
                host_thread_id=host_thread_id,
                fallback_reason=fallback_reason,
            )
        else:
            execution = self.store.create_role_execution(
                request.session_id,
                request.run_id,
                request.role,
                project_id=request.project_id,
                source_revision=request.source_revision,
                context_manifest_id=request.context_manifest_id,
                execution_mode=mode.value,
                requested_mode=request.preferred_mode.value,
                host_thread_id=host_thread_id,
                workspace_binding=self._workspace_payload(request.workspace_binding),
                fallback_reason=fallback_reason,
                idempotency_key=execution_key,
            )
        execution["capabilities"] = profile.to_dict()
        return execution

    def bind_invocation(
        self,
        session_id: str,
        role_execution_id: str,
        invocation_id: str,
    ) -> dict[str, Any]:
        """把正式 Model Invocation 绑定到同一次 Role Execution。"""

        execution = self.store.get_role_execution(session_id, role_execution_id)
        invocation = self.store.get_model_invocation(session_id, invocation_id)
        if invocation["run_id"] != execution["run_id"] or invocation["role"] != execution["role"]:
            raise RuntimeValidationError("ROLE_EXECUTION_INVOCATION_MISMATCH")
        if invocation["context_id"] != execution["context_manifest_id"] or int(invocation["source_revision"]) != int(execution["source_revision"]):
            raise RuntimeValidationError("ROLE_EXECUTION_INVOCATION_CONTEXT_MISMATCH")
        return self.store.bind_role_execution_invocation(session_id, role_execution_id, invocation_id)

    def invoke(
        self,
        session_id: str,
        role_execution_id: str,
        model_adapter: Any,
        request: Any,
        *,
        invocation_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]:
        """按实际 execution_mode 调用 Child Thread 或 Fresh Invocation。"""

        execution = self.store.get_role_execution(session_id, role_execution_id)
        if execution["status"] != "STARTED":
            raise RuntimeValidationError("ROLE_EXECUTION_NOT_ACTIVE")
        if invocation_observer is not None:
            try:
                invocation_observer(
                    {
                        "event": (
                            "actual_model_request_boundary"
                            if execution["execution_mode"] == ExecutionMode.FRESH_INVOCATION.value
                            else "role_thread_dispatch_boundary"
                        ),
                        "actual_model_request": (
                            execution["execution_mode"] == ExecutionMode.FRESH_INVOCATION.value
                        ),
                        "role_execution_id": role_execution_id,
                        "invocation_id": getattr(request, "invocation_id", ""),
                        "execution_mode": execution["execution_mode"],
                        "role": execution["role"],
                        "phase": getattr(request, "phase", "unknown"),
                        "task_id": getattr(request, "run_id", "unknown"),
                        "project_revision": getattr(request, "source_revision", 0),
                        "invocation_reason": getattr(request, "invocation_reason", "phase_execution"),
                        "context_manifest_hash": (
                            getattr(request, "context", {}).get("context_hash", "")
                            if isinstance(getattr(request, "context", {}), Mapping)
                            else ""
                        ),
                        "context_bytes": (
                            int(getattr(request, "context", {}).get("inline_bytes", 0))
                            if isinstance(getattr(request, "context", {}), Mapping)
                            else 0
                        ),
                        "execution_type": getattr(request, "execution_type", "llm"),
                    }
                )
            except Exception:
                # 观测失败不能改变正式模型调用与 Role 生命周期。
                pass
        if execution["execution_mode"] == ExecutionMode.CHILD_THREAD.value:
            handler = getattr(self.host, "invoke_child", None)
            if not callable(handler) or not execution.get("host_thread_id"):
                raise RuntimeValidationError("HOST_CHILD_THREAD_INVOKE_UNAVAILABLE")
            result = handler(str(execution["host_thread_id"]), request)
        else:
            invoke = getattr(model_adapter, "invoke", None)
            if not callable(invoke):
                raise RuntimeValidationError("FRESH_INVOCATION_ADAPTER_INVALID")
            result = invoke(request)
        if not isinstance(result, Mapping):
            raise RuntimeValidationError("MODEL_OUTPUT_INVALID")
        return result

    def complete(self, session_id: str, role_execution_id: str, *, reason: str = "completed") -> dict[str, Any]:
        execution = self.store.get_role_execution(session_id, role_execution_id)
        if execution["execution_mode"] == ExecutionMode.CHILD_THREAD.value and execution.get("host_thread_id"):
            archive = getattr(self.host, "terminate_child", None)
            if callable(archive):
                archive(str(execution["host_thread_id"]), reason=reason)
        return self.store.complete_role_execution(session_id, role_execution_id, termination_reason=reason)

    def fail(self, session_id: str, role_execution_id: str, *, reason: str) -> dict[str, Any]:
        execution = self.store.get_role_execution(session_id, role_execution_id)
        if execution["execution_mode"] == ExecutionMode.CHILD_THREAD.value and execution.get("host_thread_id"):
            terminate = getattr(self.host, "terminate_child", None)
            if callable(terminate):
                terminate(str(execution["host_thread_id"]), reason=reason)
        return self.store.fail_role_execution(session_id, role_execution_id, termination_reason=reason)

    def cancel_active(self, session_id: str, *, reason: str) -> list[str]:
        active = self.store.active_role_executions(session_id)
        cancelled: list[str] = []
        for execution in active:
            try:
                self.fail(session_id, str(execution["role_execution_id"]), reason=reason)
            except RuntimeValidationError:
                continue
            cancelled.append(str(execution["role_execution_id"]))
        return cancelled

    def recover(self, session_id: str) -> list[dict[str, Any]]:
        """把崩溃遗留的 STARTED execution 标记为 UNKNOWN_AFTER_CRASH。"""

        return self.store.recover_interrupted_role_executions(session_id)


__all__ = [
    "CORE_ROLES",
    "ExecutionMode",
    "HostCapabilityProfile",
    "RoleExecutionBroker",
    "RoleExecutionHost",
    "RoleExecutionPolicy",
    "RoleExecutionRequest",
    "UnavailableRoleExecutionHost",
    "WorkspaceBinding",
    "WorkspaceMode",
]
