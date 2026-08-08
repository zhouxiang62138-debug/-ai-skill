"""F11.1 Host-side ExecutionBroker。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from runtime.event_types import ActorType, EventType
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.leases import LeaseManager
from runtime.policy import CapabilityPolicy
from runtime.security.credentials import CredentialBroker
from runtime.security.models import CredentialRequest
from runtime.security.network import (
    ExternalToolPolicy,
    ExternalToolRequest,
    NetworkPolicy,
    NetworkRequest,
    sanitize_external_result,
)
from runtime.session_store import SessionStore

from .base import ExecutionEnvironment
from .models import (
    ExecutionContext,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionResult,
)
from .path_policy import ExecutionPathPolicy, PathAccessDenied
from .snapshots import WorkspaceSnapshotService


_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}


class ExecutionBroker:
    """唯一可以把 Role Run 连接到 ExecutionEnvironment 的 Host-side Broker。"""

    def __init__(
        self,
        store: SessionStore,
        leases: LeaseManager,
        *,
        path_policy: ExecutionPathPolicy | None = None,
        capability_policy: CapabilityPolicy | None = None,
        snapshot_service: WorkspaceSnapshotService | None = None,
    ) -> None:
        self._store = store
        self._leases = leases
        self._path_policy = path_policy or ExecutionPathPolicy()
        self._capability_policy = capability_policy or CapabilityPolicy()
        self._snapshot_service = snapshot_service or WorkspaceSnapshotService(
            store.path.parent / "snapshots", path_policy=self._path_policy
        )

    def _authorize(self, context: ExecutionContext, lease_token: str) -> dict[str, Any]:
        session = self._store.get_session(context.session_id)
        if session.project_id != context.project_id:
            raise RuntimeValidationError("EXECUTION_PROJECT_ID_MISMATCH")
        if Path(session.project_root).resolve() != Path(context.project_root).resolve():
            raise RuntimeValidationError("EXECUTION_PROJECT_ROOT_MISMATCH")
        run = self._store.get_role_run(context.session_id, context.run_id)
        if run["status"] != "STARTED":
            raise RuntimeValidationError("ROLE_RUN_INVALID_TRANSITION")
        if run["worker_id"] != context.worker_id:
            raise RuntimeValidationError("ROLE_RUN_WORKER_MISMATCH")
        if run["role"] != context.role:
            raise RuntimeValidationError("ROLE_RUN_ROLE_MISMATCH")
        self._leases.assert_valid(
            context.session_id,
            context.worker_id,
            context.lease_version,
            lease_token,
        )
        return run

    def _authorize_capability(
        self,
        context: ExecutionContext,
        capability: str,
        *,
        resource: str | None = None,
        action: str | None = None,
    ) -> str:
        """执行操作前检查 Capability，并把拒绝交给 F10 Event 体系审计。"""

        try:
            return self._capability_policy.authorize(
                context.role, capability, resource, action
            )
        except RuntimeValidationError as exc:
            self._record_capability_denied(
                context,
                capability,
                resource=resource,
                action=action,
                error_code=str(exc),
            )
            raise

    def _record_capability_denied(
        self,
        context: ExecutionContext,
        capability: str,
        *,
        resource: str | None,
        action: str | None,
        error_code: str,
    ) -> None:
        """只记录稳定标识和 hash，避免把请求上下文写入 Event。"""

        def digest(value: str | None) -> str | None:
            if value is None:
                return None
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        known_roles = self._capability_policy.role_capabilities
        known_capabilities = self._capability_policy.capabilities
        role_value = context.role if context.role in known_roles else "unknown"
        capability_value = (
            capability if capability in known_capabilities else "unknown"
        )
        audit_error_code = (
            "CAPABILITY_INPUT_REJECTED"
            if error_code == "CAPABILITY_SECRET_FORBIDDEN"
            else error_code
        )
        payload = {
            "role": role_value,
            "capability": capability_value,
            "decision": "DENY",
            "error_code": audit_error_code,
            "role_hash": digest(context.role),
            "capability_hash": digest(capability),
            "resource_hash": digest(resource),
            "action_hash": digest(action),
        }
        audit_key = hashlib.sha256(
            "\x1f".join(
                str(value)
                for value in (
                    context.run_id,
                    context.role,
                    capability,
                    resource,
                    action,
                    error_code,
                )
            ).encode("utf-8")
        ).hexdigest()
        self._store.append_event(
            context.session_id,
            EventType.CAPABILITY_DENIED,
            ActorType.ROLE,
            role_value,
            idempotency_key=f"capability-denied:{audit_key}",
            correlation_id=context.run_id,
            payload=payload,
        )

    def _record_path_denied(
        self, context: ExecutionContext, error: PathAccessDenied
    ) -> None:
        """由可信 Broker 记录一次统一的 Path DENY，后端不直接接触 SessionStore。"""

        audit_key = hashlib.sha256(
            "\x1f".join(
                (
                    context.run_id,
                    error.operation,
                    error.resource_reference,
                    error.reason_code,
                )
            ).encode("utf-8")
        ).hexdigest()
        self._store.append_event(
            context.session_id,
            EventType.PATH_ACCESS_DENIED,
            ActorType.ROLE,
            context.role,
            idempotency_key=f"path-denied:{audit_key}",
            correlation_id=context.run_id,
            payload={
                "role": context.role,
                "operation": error.operation,
                "resource_reference": error.resource_reference,
                "resource_class": error.resource_class,
                "policy_source": error.policy_source,
                "reason_code": error.reason_code,
                "decision": "DENY",
                "run_id": context.run_id,
            },
        )

    def _audit_path_denial(
        self, context: ExecutionContext, error: PathAccessDenied
    ) -> None:
        try:
            self._record_path_denied(context, error)
        except Exception as exc:
            # 已经确定为 DENY 时，审计失败只能继续拒绝并暴露持久化错误。
            raise RuntimeStorageError("PATH_DENIAL_AUDIT_PERSISTENCE_FAILED") from exc

    def _assert_path(
        self,
        context: ExecutionContext,
        path: str,
        *,
        operation: str,
    ) -> Path:
        try:
            return self._path_policy.assert_path(
                context.role,
                context.project_root,
                path,
                operation=operation,
            )
        except PathAccessDenied as exc:
            self._audit_path_denial(context, exc)
            raise

    def _assert_cwd(self, context: ExecutionContext, cwd: str) -> Path:
        try:
            return self._path_policy.assert_cwd(context.project_root, cwd)
        except PathAccessDenied as exc:
            self._audit_path_denial(context, exc)
            raise

    def _record_credential_event(
        self,
        context: ExecutionContext,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        """通过 F10 SessionStore 追加凭据审计，不保存 provider 返回值。"""

        event_hash = hashlib.sha256(
            json.dumps(
                {"event_type": event_type.value, "payload": payload},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._store.append_event(
            context.session_id,
            event_type,
            ActorType.ROLE,
            str(payload.get("role", "unknown")),
            idempotency_key=f"credential:{event_type.value}:{event_hash}",
            correlation_id=context.run_id,
            payload=payload,
        )

    @staticmethod
    def _request_arguments(request: ExecutionRequest) -> dict[str, Any]:
        profile = request.resolved_profile()
        return {
            "logical_call_id": request.logical_call_id,
            "argv": list(request.argv),
            "cwd": request.cwd,
            "timeout": request.timeout,
            "execution_profile": profile.name,
            "code_snapshot_hash": profile.code_snapshot_hash,
            "environment_hash": profile.environment_hash,
        }

    def provision(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        *,
        lease_token: str,
    ) -> None:
        self._authorize(context, lease_token)
        self._authorize_capability(context, "process.execute", action="provision")
        environment.provision(context)

    def execute(
        self,
        context: ExecutionContext,
        request: ExecutionRequest,
        environment: ExecutionEnvironment,
        *,
        lease_token: str,
    ) -> ExecutionReceipt:
        """创建/重放 Tool Call，并通过唯一完成路径写入不可变结果。"""

        self._authorize(context, lease_token)
        self._authorize_capability(context, "process.execute", action="execute")
        self._assert_cwd(context, request.cwd)
        profile = request.resolved_profile()
        arguments = self._request_arguments(request)
        call = self._store.request_tool_call(
            context.session_id,
            tool_name="execution",
            arguments=arguments,
            idempotency_key=f"execution:{request.logical_call_id}",
        )
        record = self._store.get_tool_call(context.session_id, call)
        if record["status"] in _TERMINAL:
            return self._receipt_from_record(request, record)
        if record["status"] == "UNKNOWN_AFTER_CRASH":
            raise RuntimeValidationError("TOOL_CALL_REQUIRES_RECOVERY")
        if record["status"] == "STARTED":
            raise RuntimeValidationError("TOOL_CALL_IN_PROGRESS")
        attempt_id = self._store.start_tool_call(
            context.session_id,
            call,
            code_snapshot_hash=profile.code_snapshot_hash,
            environment_hash=profile.environment_hash,
        )
        try:
            result = environment.execute(context, request)
        except Exception as exc:
            # 后端异常也必须有不可变终态；只保存异常类型，避免把潜在 Secret 写入结果。
            result = ExecutionResult(
                exit_code=None,
                timed_out=False,
                stderr=f"Execution backend failed: {type(exc).__name__}",
            )
        if not isinstance(result, ExecutionResult):
            raise RuntimeValidationError("EXECUTION_RESULT_INVALID")
        payload = result.to_payload(
            logical_call_id=request.logical_call_id,
            argv=request.argv,
            cwd=request.cwd,
        )
        reference, result_hash = self._store.write_tool_result(call, payload)
        status = (
            "TIMED_OUT"
            if result.timed_out
            else "SUCCEEDED"
            if result.exit_code == 0
            else "FAILED"
        )
        # complete_tool_call 已在同一事务完成 Attempt 与唯一 terminal Event。
        self._store.complete_tool_call(
            context.session_id,
            call,
            result_reference=reference,
            result_hash=result_hash,
            status=status,
            attempt_id=attempt_id,
        )
        return ExecutionReceipt(
            logical_call_id=request.logical_call_id,
            tool_call_id=call,
            attempt_id=attempt_id,
            status=status,
            result_reference=reference,
            result_hash=result_hash,
            result=result,
        )

    def request_credential(
        self,
        context: ExecutionContext,
        request: CredentialRequest,
        credential_broker: CredentialBroker,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """通过 CapabilityPolicy 后调用 Host-side CredentialBroker。"""

        self._authorize(context, lease_token)

        def audit(event_type: EventType, payload: dict[str, Any]) -> None:
            self._record_credential_event(context, event_type, payload)

        return credential_broker.invoke(context.role, request, audit=audit)

    def request_network(
        self,
        context: ExecutionContext,
        request: NetworkRequest,
        network_policy: NetworkPolicy,
        *,
        lease_token: str,
    ) -> str:
        """只执行可复用的 Network Policy 校验，不声称提供网络 Sandbox。"""

        self._authorize(context, lease_token)

        def audit(event_type: EventType, payload: dict[str, Any]) -> None:
            self._record_credential_event(context, event_type, payload)

        return network_policy.authorize(
            context.role,
            request.service,
            request.operation,
            request.url,
            audit=audit,
        )

    def invoke_external(
        self,
        context: ExecutionContext,
        request: ExternalToolRequest,
        external_tool_policy: ExternalToolPolicy,
        handler: Callable[
            [ExternalToolRequest, Mapping[str, Any] | None], Mapping[str, Any]
        ],
        *,
        lease_token: str,
        credential_broker: CredentialBroker | None = None,
        credential_request: CredentialRequest | None = None,
    ) -> dict[str, Any]:
        """按 Capability → Tool Policy → Credential Broker → Handler 执行。"""

        self._authorize(context, lease_token)

        def policy_audit(event_type: EventType, payload: dict[str, Any]) -> None:
            self._record_credential_event(context, event_type, payload)

        external_tool_policy.authorize(
            context.role,
            request.tool,
            request.operation,
            request.resource,
            audit=policy_audit,
        )
        if (credential_broker is None) != (credential_request is None):
            raise RuntimeValidationError("CREDENTIAL_REQUEST_REQUIRED")
        credential_result: Mapping[str, Any] | None = None
        if credential_broker is not None and credential_request is not None:
            credential_result = credential_broker.invoke(
                context.role,
                credential_request,
                audit=policy_audit,
            )
        try:
            raw_result = handler(request, credential_result)
        except Exception:
            raise RuntimeValidationError("EXTERNAL_TOOL_PROVIDER_FAILED") from None
        return sanitize_external_result(raw_result)

    def read_file(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        path: str,
        *,
        lease_token: str,
    ) -> str:
        self._authorize(context, lease_token)
        self._authorize_capability(
            context, "filesystem.read", resource=path, action="read"
        )
        self._assert_path(context, path, operation="read")
        return environment.read_file(context, path)

    def write_file(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        path: str,
        content: str,
        *,
        lease_token: str,
    ) -> None:
        self._authorize(context, lease_token)
        self._authorize_capability(
            context, "filesystem.write", resource=path, action="write"
        )
        self._assert_path(context, path, operation="write")
        environment.write_file(context, path, content)

    def list_files(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        path: str = ".",
        *,
        lease_token: str,
    ) -> list[str]:
        self._authorize(context, lease_token)
        self._authorize_capability(
            context, "filesystem.read", resource=path, action="read"
        )
        self._assert_path(context, path, operation="read")
        return environment.list_files(context, path)

    def snapshot(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        *,
        lease_token: str,
    ) -> str:
        self._authorize(context, lease_token)
        self._authorize_capability(
            context, "filesystem.read", action="snapshot"
        )
        return self._snapshot_service.create(context)

    def restore(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        snapshot_id: str,
        *,
        lease_token: str,
    ) -> None:
        self._authorize(context, lease_token)
        self._authorize_capability(
            context, "filesystem.write", resource=snapshot_id, action="restore"
        )
        self._snapshot_service.restore(context, snapshot_id)
        environment.provision(context)

    def terminate(
        self,
        context: ExecutionContext,
        environment: ExecutionEnvironment,
        *,
        lease_token: str,
    ) -> None:
        self._authorize(context, lease_token)
        self._authorize_capability(context, "process.execute", action="terminate")
        environment.terminate(context)

    def _receipt_from_record(
        self, request: ExecutionRequest, record: dict[str, Any]
    ) -> ExecutionReceipt:
        reference = record.get("result_reference")
        result_hash = record.get("result_hash")
        if not isinstance(reference, str) or not isinstance(result_hash, str):
            raise RuntimeStorageError("TOOL_RESULT_MISSING")
        payload = self._store.read_tool_result(reference, result_hash)
        return ExecutionReceipt(
            logical_call_id=request.logical_call_id,
            tool_call_id=str(record["tool_call_id"]),
            attempt_id=None,
            status=str(record["status"]),
            result_reference=reference,
            result_hash=result_hash,
            result=ExecutionResult.from_payload(payload),
        )
