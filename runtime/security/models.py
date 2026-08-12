"""F12.2 Credential Broker 的无凭据请求模型。"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from runtime.errors import RuntimeValidationError


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|token|secret|password)"
    r"\s*[:=]|(?:\b(?:api[_-]?key|access[_-]?token|private[_-]?key|token|secret|password)\b)"
    r"|(?:bearer\s+|ghp_|github_pat_|sk-[A-Za-z0-9])"
)
_SENSITIVE_FIELDS = re.compile(
    r"(?i)(?:authorization|api[_-]?key|access[_-]?token|credential|private[_-]?key|"
    r"password|secret|token)"
)
_COMMAND_FIELDS = frozenset(
    {"argv", "cmd", "command", "raw_command", "raw_shell", "shell"}
)


def validate_identifier(value: object, name: str) -> str:
    """校验不会承载 Secret 的结构化标识。"""

    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    if _SECRET_MARKER.search(value):
        raise RuntimeValidationError("CREDENTIAL_REQUEST_SECRET_FORBIDDEN")
    return value


def _validate_text(value: object, name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")
    if _SECRET_MARKER.search(value):
        raise RuntimeValidationError("CREDENTIAL_REQUEST_SECRET_FORBIDDEN")
    return value


def _copy_structured(value: object, *, field_name: str) -> Any:
    """复制并校验 JSON-like 参数，拒绝凭据字段和任意命令字段。"""

    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")
            normalized = key.casefold().replace("-", "_")
            if normalized in _COMMAND_FIELDS:
                raise RuntimeValidationError("CREDENTIAL_REQUEST_COMMAND_FORBIDDEN")
            if _SENSITIVE_FIELDS.search(key):
                raise RuntimeValidationError("CREDENTIAL_REQUEST_SENSITIVE_FIELD")
            copied[key] = _copy_structured(item, field_name=key)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_structured(item, field_name=field_name) for item in value]
    if isinstance(value, str):
        return _validate_text(value, field_name, max_length=4096)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")


class CredentialProvider(Protocol):
    """可信 Host-side provider 的最小回调协议。"""

    def __call__(
        self, request: "CredentialRequest", secret: str
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CredentialRequest:
    """Role 可提交的结构化服务请求，不包含 credential value。"""

    credential_id: str
    service: str
    operation: str
    resource: str | None = field(default=None, repr=False)
    parameters: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        validate_identifier(self.credential_id, "credential_id")
        validate_identifier(self.service, "service")
        validate_identifier(self.operation, "operation")
        if self.resource is not None:
            _validate_text(self.resource, "resource", max_length=1024)
        if not isinstance(self.parameters, Mapping):
            raise RuntimeValidationError("CREDENTIAL_REQUEST_INVALID")
        copied = _copy_structured(self.parameters, field_name="parameters")
        object.__setattr__(self, "parameters", MappingProxyType(copied))
