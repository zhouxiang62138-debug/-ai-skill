"""F12.2 Host-side Credential Broker。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from runtime.event_types import EventType
from runtime.errors import RuntimeValidationError
from runtime.policy import CapabilityPolicy

from .models import (
    CredentialProvider,
    CredentialRequest,
    _SECRET_MARKER,
    validate_identifier,
)


REDACTED = "[REDACTED]"
AuditSink = Callable[[EventType, Mapping[str, Any]], None]
_RESULT_SENSITIVE_FIELD = re.compile(
    r"(?i)(?:authorization|api[_-]?key|access[_-]?token|credential|private[_-]?key|"
    r"password|secret|token)"
)
_SAFE_AUDIT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def redact_secret(value: str, secret: str) -> str:
    """按已知 Secret 精确替换文本；不做模糊猜测。"""

    if not isinstance(value, str):
        raise RuntimeValidationError("CREDENTIAL_TEXT_INVALID")
    if not isinstance(secret, str) or not secret:
        raise RuntimeValidationError("CREDENTIAL_SECRET_INVALID")
    return value.replace(secret, REDACTED)


class _Registration:
    """只在 Host 内存中保存 Secret；repr 永远不显示 Secret。"""

    __slots__ = ("credential_id", "service", "allowed_operations", "provider", "_secret")

    def __init__(
        self,
        credential_id: str,
        service: str,
        allowed_operations: frozenset[str],
        provider: CredentialProvider | object,
        secret: str,
    ) -> None:
        self.credential_id = credential_id
        self.service = service
        self.allowed_operations = allowed_operations
        self.provider = provider
        self._secret = secret

    def secret(self) -> str:
        """仅供同一 Host-side Broker 调用 provider。"""

        return self._secret

    def __repr__(self) -> str:
        return (
            f"_Registration(credential_id={self.credential_id!r}, "
            f"service={self.service!r}, allowed_operations={self.allowed_operations!r})"
        )


def _contains_exact_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return bool(secret and secret in value)
    if isinstance(value, Mapping):
        return any(
            _contains_exact_secret(key, secret) or _contains_exact_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_secret(item, secret) for item in value)
    return False


def _sanitize_result(value: object, secret: str) -> Any:
    """返回 JSON-like 且已完成精确 Secret redaction 的结果。"""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise RuntimeValidationError("CREDENTIAL_RESULT_INVALID")
            if secret and secret in key:
                raise RuntimeValidationError("CREDENTIAL_RESULT_SECRET_FORBIDDEN")
            if _RESULT_SENSITIVE_FIELD.search(key):
                raise RuntimeValidationError("CREDENTIAL_RESULT_SENSITIVE_FIELD")
            sanitized[key] = _sanitize_result(item, secret)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_result(item, secret) for item in value]
    if isinstance(value, str):
        return redact_secret(value, secret)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise RuntimeValidationError("CREDENTIAL_RESULT_INVALID")


class CredentialBroker:
    """Host-side 凭据边界；Role 只能提交请求，不能读取注册的 Secret。"""

    def __init__(self, capability_policy: CapabilityPolicy | None = None) -> None:
        self._capability_policy = capability_policy or CapabilityPolicy()
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        *,
        credential_id: str,
        service: str,
        allowed_operations: Iterable[str],
        provider: CredentialProvider | object,
        secret: str,
    ) -> None:
        """由可信 Host 注册内存凭据；配置文件不参与保存 Secret。"""

        credential_id = validate_identifier(credential_id, "credential_id")
        service = validate_identifier(service, "service")
        if isinstance(allowed_operations, (str, bytes)):
            raise RuntimeValidationError("CREDENTIAL_OPERATION_POLICY_INVALID")
        try:
            operations = frozenset(
                validate_identifier(operation, "operation")
                for operation in allowed_operations
            )
        except TypeError as exc:
            raise RuntimeValidationError("CREDENTIAL_OPERATION_POLICY_INVALID") from exc
        if not operations:
            raise RuntimeValidationError("CREDENTIAL_OPERATION_POLICY_INVALID")
        if not isinstance(secret, str) or not secret:
            raise RuntimeValidationError("CREDENTIAL_SECRET_INVALID")
        if not callable(provider) and not callable(getattr(provider, "invoke", None)):
            raise RuntimeValidationError("CREDENTIAL_PROVIDER_INVALID")
        if credential_id in self._registrations:
            raise RuntimeValidationError("CREDENTIAL_ALREADY_REGISTERED")
        self._registrations[credential_id] = _Registration(
            credential_id, service, operations, provider, secret
        )

    def register_provider(self, **kwargs: Any) -> None:
        """register 的语义别名，便于 Host 侧按 provider 术语调用。"""

        self.register(**kwargs)

    @property
    def credential_ids(self) -> tuple[str, ...]:
        """只暴露标识，不暴露任何 Secret 或 provider 内部状态。"""

        return tuple(sorted(self._registrations))

    @staticmethod
    def _audit_value(value: str, *, unknown: str = "unknown") -> str:
        if (
            isinstance(value, str)
            and _SAFE_AUDIT_IDENTIFIER.fullmatch(value)
            and not _SECRET_MARKER.search(value)
        ):
            return value
        return unknown

    @classmethod
    def _audit_payload(
        cls,
        *,
        role: str,
        request: CredentialRequest,
        decision: str,
        error_code: str | None = None,
    ) -> dict[str, str]:
        payload = {
            "role": cls._audit_value(role),
            "service": cls._audit_value(request.service),
            "operation": cls._audit_value(request.operation),
            "credential_id": cls._audit_value(request.credential_id),
            "decision": decision,
        }
        if error_code is not None:
            payload["error_code"] = (
                "CREDENTIAL_INPUT_REJECTED"
                if _SECRET_MARKER.search(error_code)
                else error_code
            )
        return payload

    @staticmethod
    def _emit(
        audit: AuditSink | None,
        event_type: EventType,
        payload: Mapping[str, Any],
    ) -> None:
        if audit is not None:
            audit(event_type, dict(payload))

    def invoke(
        self,
        role: str,
        request: CredentialRequest,
        *,
        audit: AuditSink | None = None,
    ) -> dict[str, Any]:
        """校验 Capability、credential 和 operation 后调用可信 provider。"""

        if not isinstance(request, CredentialRequest):
            raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")
        self._emit(
            audit,
            EventType.CREDENTIAL_REQUESTED,
            self._audit_payload(
                role=role, request=request, decision="REQUESTED"
            ),
        )

        try:
            self._capability_policy.authorize(
                role,
                "credential.use",
                resource=request.service,
                action=request.operation,
            )
        except RuntimeValidationError as exc:
            payload = self._audit_payload(
                role=role,
                request=request,
                decision="DENY",
                error_code="CREDENTIAL_CAPABILITY_DENIED",
            )
            self._emit(audit, EventType.CREDENTIAL_DENIED, payload)
            raise RuntimeValidationError("CREDENTIAL_CAPABILITY_DENIED") from exc

        registration = self._registrations.get(request.credential_id)
        if registration is None or registration.service != request.service:
            self._emit(
                audit,
                EventType.CREDENTIAL_DENIED,
                self._audit_payload(
                    role=role,
                    request=request,
                    decision="DENY",
                    error_code="UNKNOWN_CREDENTIAL",
                ),
            )
            raise RuntimeValidationError("UNKNOWN_CREDENTIAL")
        if request.operation not in registration.allowed_operations:
            self._emit(
                audit,
                EventType.CREDENTIAL_DENIED,
                self._audit_payload(
                    role=role,
                    request=request,
                    decision="DENY",
                    error_code="CREDENTIAL_OPERATION_DENIED",
                ),
            )
            raise RuntimeValidationError("CREDENTIAL_OPERATION_DENIED")
        if (
            _contains_exact_secret(request.credential_id, registration.secret())
            or _contains_exact_secret(request.service, registration.secret())
            or _contains_exact_secret(request.operation, registration.secret())
            or _contains_exact_secret(request.resource, registration.secret())
            or _contains_exact_secret(request.parameters, registration.secret())
        ):
            self._emit(
                audit,
                EventType.CREDENTIAL_DENIED,
                self._audit_payload(
                    role=role,
                    request=request,
                    decision="DENY",
                    error_code="CREDENTIAL_REQUEST_SECRET_FORBIDDEN",
                ),
            )
            raise RuntimeValidationError("CREDENTIAL_REQUEST_SECRET_FORBIDDEN")

        try:
            if callable(registration.provider):
                raw_result = registration.provider(request, registration.secret())
            else:
                raw_result = registration.provider.invoke(
                    request, registration.secret()
                )
            if not isinstance(raw_result, Mapping):
                raise RuntimeValidationError("CREDENTIAL_RESULT_INVALID")
            result = _sanitize_result(raw_result, registration.secret())
        except RuntimeValidationError as exc:
            error_code = str(exc)
            if error_code not in {
                "CREDENTIAL_RESULT_INVALID",
                "CREDENTIAL_RESULT_SENSITIVE_FIELD",
                "CREDENTIAL_RESULT_SECRET_FORBIDDEN",
            }:
                error_code = "CREDENTIAL_PROVIDER_FAILED"
            self._emit(
                audit,
                EventType.CREDENTIAL_DENIED,
                self._audit_payload(
                    role=role,
                    request=request,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code) from None
        except Exception:
            self._emit(
                audit,
                EventType.CREDENTIAL_DENIED,
                self._audit_payload(
                    role=role,
                    request=request,
                    decision="DENY",
                    error_code="CREDENTIAL_PROVIDER_FAILED",
                ),
            )
            raise RuntimeValidationError("CREDENTIAL_PROVIDER_FAILED") from None

        self._emit(
            audit,
            EventType.CREDENTIAL_ALLOWED,
            self._audit_payload(role=role, request=request, decision="ALLOW"),
        )
        return result
