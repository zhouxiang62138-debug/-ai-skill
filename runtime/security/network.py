"""F12.3 确定性的 Network 与 External Tool Policy。"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from typing import Any

from scripts.project_state import parse_project_yaml

from runtime.errors import RuntimeValidationError
from runtime.policy import (
    CAPABILITY_ALLOW,
    CAPABILITY_DENY,
    CapabilityPolicy,
)
from runtime.event_types import EventType

from .models import _SECRET_MARKER, validate_identifier


_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE_QUERY = re.compile(
    r"(?i)(?:authorization|api[_-]?key|access[_-]?token|credential|password|secret|token)"
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_BLOCKED_HOSTS = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)

AuditSink = Callable[[EventType, Mapping[str, Any]], None]


def _config_path(config_path: str | Path | None) -> Path:
    return Path(config_path) if config_path else _ROOT / "config" / "role_policies.yaml"


def _load_section(config_path: str | Path | None, section_name: str) -> dict[str, Any]:
    document = parse_project_yaml(_config_path(config_path).read_text(encoding="utf-8"))
    section = document.get(section_name)
    if not isinstance(section, dict) or section.get("default") != "deny":
        raise RuntimeValidationError("SECURITY_POLICY_INVALID")
    return section


def normalize_host(host: str) -> str:
    """规范化域名或 IP，保留精确匹配语义并去掉 DNS 末尾点。"""

    if not isinstance(host, str) or not host or any(char.isspace() for char in host):
        raise RuntimeValidationError("NETWORK_HOST_INVALID")
    value = host.rstrip(".").casefold()
    if not value:
        raise RuntimeValidationError("NETWORK_HOST_INVALID")
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RuntimeValidationError("NETWORK_HOST_INVALID") from exc


def _is_blocked_host(host: str) -> bool:
    normalized = normalize_host(host)
    if normalized in _BLOCKED_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_reserved
        or address.is_multicast
    )


def _audit_value(value: object) -> str:
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        if not _SECRET_MARKER.search(value):
            return value
    return "unknown"


def _audit_payload(
    *,
    role: str,
    capability: str,
    service: str,
    operation: str,
    host: str | None,
    decision: str,
    error_code: str | None = None,
) -> dict[str, str]:
    payload = {
        "role": _audit_value(role),
        "capability": _audit_value(capability),
        "service": _audit_value(service),
        "operation": _audit_value(operation),
        "host": _audit_value(host or "unknown"),
        "decision": decision,
    }
    if error_code is not None:
        payload["error_code"] = error_code
    return payload


def _emit(
    audit: AuditSink | None,
    event_type: EventType,
    payload: Mapping[str, Any],
) -> None:
    if audit is not None:
        audit(event_type, dict(payload))


def _parse_list(value: object, error_code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RuntimeValidationError(error_code)
    return tuple(value)


def _parse_ports(value: object) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise RuntimeValidationError("NETWORK_PORT_POLICY_INVALID")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise RuntimeValidationError("NETWORK_PORT_POLICY_INVALID")
    ports = frozenset(value)
    if not all(1 <= port <= 65535 for port in ports):
        raise RuntimeValidationError("NETWORK_PORT_POLICY_INVALID")
    return ports


@dataclass(frozen=True)
class NetworkRequest:
    """可审计的 URL 请求描述；不承载 Authorization 或 credential value。"""

    service: str
    operation: str
    url: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_identifier(self.service, "service")
        validate_identifier(self.operation, "operation")
        if not isinstance(self.url, str) or not self.url or len(self.url) > 4096:
            raise RuntimeValidationError("NETWORK_URL_INVALID")
        if "\x00" in self.url or "\r" in self.url or "\n" in self.url:
            raise RuntimeValidationError("NETWORK_URL_INVALID")


class NetworkPolicy:
    """先过 network.access，再执行 service、host、scheme 和 port allowlist。"""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        section = _load_section(config_path, "network_policy")
        self._capability_policy = capability_policy or CapabilityPolicy(config_path)
        self._allowed_schemes = frozenset(
            scheme.casefold()
            for scheme in _parse_list(
                section.get("allowed_schemes"), "NETWORK_SCHEME_POLICY_INVALID"
            )
        )
        if not self._allowed_schemes:
            raise RuntimeValidationError("NETWORK_SCHEME_POLICY_INVALID")
        raw_services = section.get("services")
        raw_roles = section.get("roles", {})
        if not isinstance(raw_services, dict) or not isinstance(raw_roles, dict):
            raise RuntimeValidationError("NETWORK_POLICY_INVALID")
        services: dict[str, dict[str, Any]] = {}
        for service, policy in raw_services.items():
            if not isinstance(service, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("NETWORK_POLICY_INVALID")
            hosts = _parse_list(policy.get("hosts"), "NETWORK_HOST_POLICY_INVALID")
            if not hosts:
                raise RuntimeValidationError("NETWORK_HOST_POLICY_INVALID")
            ports = _parse_ports(policy.get("ports", [443]))
            operations = _parse_list(
                policy.get("operations"), "NETWORK_OPERATION_POLICY_INVALID"
            )
            services[service] = {
                "hosts": frozenset(normalize_host(host) for host in hosts),
                "ports": ports,
                "operations": frozenset(operations),
            }
        role_services: dict[str, frozenset[str]] = {}
        for role, policy in raw_roles.items():
            if not isinstance(role, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("NETWORK_POLICY_INVALID")
            role_services[role] = frozenset(
                _parse_list(policy.get("services", []), "NETWORK_ROLE_POLICY_INVALID")
            )
        self._services = services
        self._role_services = role_services

    @staticmethod
    def _parse_url(url: str, allowed_schemes: frozenset[str]) -> tuple[str, int]:
        try:
            parsed = urlsplit(url)
            scheme = parsed.scheme.casefold()
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise RuntimeValidationError("NETWORK_URL_INVALID") from exc
        if scheme not in allowed_schemes:
            raise RuntimeValidationError("NETWORK_SCHEME_DENIED")
        if not host:
            raise RuntimeValidationError("NETWORK_URL_INVALID")
        if parsed.username is not None or parsed.password is not None:
            raise RuntimeValidationError("NETWORK_URL_CREDENTIALS_FORBIDDEN")
        if parsed.fragment:
            raise RuntimeValidationError("NETWORK_URL_FRAGMENT_FORBIDDEN")
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if _SENSITIVE_QUERY.search(key) or _SECRET_MARKER.search(value):
                raise RuntimeValidationError("NETWORK_SENSITIVE_QUERY_FORBIDDEN")
        normalized_host = normalize_host(host)
        if _is_blocked_host(normalized_host):
            raise RuntimeValidationError("NETWORK_PRIVATE_HOST_DENIED")
        effective_port = port if port is not None else 443 if scheme == "https" else 0
        if effective_port <= 0:
            raise RuntimeValidationError("NETWORK_PORT_INVALID")
        return normalized_host, effective_port

    def authorize(
        self,
        role: str,
        service: str,
        operation: str,
        url: str,
        *,
        audit: AuditSink | None = None,
    ) -> str:
        """返回 ALLOW；未通过任一层时返回稳定 DENY 错误。"""

        host: str | None = None
        try:
            self._capability_policy.authorize(
                role, "network.access", resource=service, action=operation
            )
        except RuntimeValidationError as exc:
            payload = _audit_payload(
                role=role,
                capability="network.access",
                service=service,
                operation=operation,
                host=None,
                decision="DENY",
                error_code="NETWORK_CAPABILITY_DENIED",
            )
            _emit(audit, EventType.NETWORK_DENIED, payload)
            raise RuntimeValidationError("NETWORK_CAPABILITY_DENIED") from exc

        if service not in self._services:
            payload = _audit_payload(
                role=role,
                capability="network.access",
                service=service,
                operation=operation,
                host=None,
                decision="DENY",
                error_code="UNKNOWN_NETWORK_SERVICE",
            )
            _emit(audit, EventType.NETWORK_DENIED, payload)
            raise RuntimeValidationError("UNKNOWN_NETWORK_SERVICE")
        if service not in self._role_services.get(role, frozenset()):
            error_code = "NETWORK_SERVICE_DENIED"
            _emit(
                audit,
                EventType.NETWORK_DENIED,
                _audit_payload(
                    role=role,
                    capability="network.access",
                    service=service,
                    operation=operation,
                    host=None,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        policy = self._services[service]
        if operation not in policy["operations"]:
            error_code = "NETWORK_OPERATION_DENIED"
            _emit(
                audit,
                EventType.NETWORK_DENIED,
                _audit_payload(
                    role=role,
                    capability="network.access",
                    service=service,
                    operation=operation,
                    host=None,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        try:
            host, port = self._parse_url(url, self._allowed_schemes)
        except RuntimeValidationError as exc:
            error_code = str(exc)
            _emit(
                audit,
                EventType.NETWORK_DENIED,
                _audit_payload(
                    role=role,
                    capability="network.access",
                    service=service,
                    operation=operation,
                    host=host,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise
        if host not in policy["hosts"]:
            error_code = "NETWORK_HOST_DENIED"
            _emit(
                audit,
                EventType.NETWORK_DENIED,
                _audit_payload(
                    role=role,
                    capability="network.access",
                    service=service,
                    operation=operation,
                    host=host,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        if port not in policy["ports"]:
            error_code = "NETWORK_PORT_DENIED"
            _emit(
                audit,
                EventType.NETWORK_DENIED,
                _audit_payload(
                    role=role,
                    capability="network.access",
                    service=service,
                    operation=operation,
                    host=host,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        _emit(
            audit,
            EventType.NETWORK_ALLOWED,
            _audit_payload(
                role=role,
                capability="network.access",
                service=service,
                operation=operation,
                host=host,
                decision="ALLOW",
            ),
        )
        return CAPABILITY_ALLOW

    def check(self, role: str, service: str, operation: str, url: str) -> str:
        try:
            return self.authorize(role, service, operation, url)
        except RuntimeValidationError:
            return CAPABILITY_DENY


@dataclass(frozen=True)
class ExternalToolRequest:
    """外部工具的结构化资源请求，不接受 argv、shell 或 raw command。"""

    tool: str
    operation: str
    resource: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_identifier(self.tool, "tool")
        validate_identifier(self.operation, "operation")
        if self.resource is not None:
            if not isinstance(self.resource, str) or not self.resource or len(self.resource) > 1024:
                raise RuntimeValidationError("EXTERNAL_TOOL_RESOURCE_INVALID")
            if "\x00" in self.resource or "\r" in self.resource or "\n" in self.resource:
                raise RuntimeValidationError("EXTERNAL_TOOL_RESOURCE_INVALID")
            if any(marker in self.resource for marker in (";", "|", "&", "`", "$")):
                raise RuntimeValidationError("EXTERNAL_TOOL_COMMAND_FORBIDDEN")
            if _SECRET_MARKER.search(self.resource):
                raise RuntimeValidationError("EXTERNAL_TOOL_SECRET_FORBIDDEN")


class ExternalToolPolicy:
    """先过 external_tool.invoke，再执行 tool、role、operation allowlist。"""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        section = _load_section(config_path, "external_tool_policy")
        self._capability_policy = capability_policy or CapabilityPolicy(config_path)
        raw_tools = section.get("tools")
        raw_roles = section.get("roles", {})
        if not isinstance(raw_tools, dict) or not isinstance(raw_roles, dict):
            raise RuntimeValidationError("EXTERNAL_TOOL_POLICY_INVALID")
        tools: dict[str, dict[str, Any]] = {}
        for tool, policy in raw_tools.items():
            if not isinstance(tool, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("EXTERNAL_TOOL_POLICY_INVALID")
            operations = _parse_list(
                policy.get("operations"), "EXTERNAL_TOOL_OPERATION_POLICY_INVALID"
            )
            resources_raw = policy.get("resources")
            resources = (
                None
                if resources_raw is None
                else frozenset(
                    _parse_list(resources_raw, "EXTERNAL_TOOL_RESOURCE_POLICY_INVALID")
                )
            )
            tools[tool] = {
                "operations": frozenset(operations),
                "resources": resources,
            }
        role_tools: dict[str, frozenset[str]] = {}
        for role, policy in raw_roles.items():
            if not isinstance(role, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("EXTERNAL_TOOL_POLICY_INVALID")
            role_tools[role] = frozenset(
                _parse_list(policy.get("tools", []), "EXTERNAL_TOOL_ROLE_POLICY_INVALID")
            )
        self._tools = tools
        self._role_tools = role_tools

    def authorize(
        self,
        role: str,
        tool: str,
        operation: str,
        resource: str | None = None,
        *,
        audit: AuditSink | None = None,
    ) -> str:
        """返回 ALLOW；工具与操作均必须显式命中配置。"""

        try:
            self._capability_policy.authorize(
                role, "external_tool.invoke", resource=tool, action=operation
            )
        except RuntimeValidationError as exc:
            _emit(
                audit,
                EventType.EXTERNAL_TOOL_DENIED,
                _audit_payload(
                    role=role,
                    capability="external_tool.invoke",
                    service=tool,
                    operation=operation,
                    host=resource,
                    decision="DENY",
                    error_code="EXTERNAL_TOOL_CAPABILITY_DENIED",
                ),
            )
            raise RuntimeValidationError("EXTERNAL_TOOL_CAPABILITY_DENIED") from exc
        if tool not in self._tools:
            error_code = "UNKNOWN_EXTERNAL_TOOL"
            _emit(
                audit,
                EventType.EXTERNAL_TOOL_DENIED,
                _audit_payload(
                    role=role,
                    capability="external_tool.invoke",
                    service=tool,
                    operation=operation,
                    host=resource,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        if tool not in self._role_tools.get(role, frozenset()):
            error_code = "EXTERNAL_TOOL_DENIED"
            _emit(
                audit,
                EventType.EXTERNAL_TOOL_DENIED,
                _audit_payload(
                    role=role,
                    capability="external_tool.invoke",
                    service=tool,
                    operation=operation,
                    host=resource,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        policy = self._tools[tool]
        if operation not in policy["operations"]:
            error_code = "EXTERNAL_TOOL_OPERATION_DENIED"
            _emit(
                audit,
                EventType.EXTERNAL_TOOL_DENIED,
                _audit_payload(
                    role=role,
                    capability="external_tool.invoke",
                    service=tool,
                    operation=operation,
                    host=resource,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        resources = policy["resources"]
        if resources is not None and resource not in resources:
            error_code = "EXTERNAL_TOOL_RESOURCE_DENIED"
            _emit(
                audit,
                EventType.EXTERNAL_TOOL_DENIED,
                _audit_payload(
                    role=role,
                    capability="external_tool.invoke",
                    service=tool,
                    operation=operation,
                    host=resource,
                    decision="DENY",
                    error_code=error_code,
                ),
            )
            raise RuntimeValidationError(error_code)
        _emit(
            audit,
            EventType.EXTERNAL_TOOL_ALLOWED,
            _audit_payload(
                role=role,
                capability="external_tool.invoke",
                service=tool,
                operation=operation,
                host=resource,
                decision="ALLOW",
            ),
        )
        return CAPABILITY_ALLOW

    def check(
        self, role: str, tool: str, operation: str, resource: str | None = None
    ) -> str:
        try:
            return self.authorize(role, tool, operation, resource)
        except RuntimeValidationError:
            return CAPABILITY_DENY


def authorize_redirect(
    policy: NetworkPolicy,
    role: str,
    service: str,
    operation: str,
    redirect_url: str,
    *,
    audit: AuditSink | None = None,
) -> str:
    """重定向目标必须重新走完整 Network Policy。"""

    return policy.authorize(
        role, service, operation, redirect_url, audit=audit
    )


def sanitize_external_result(value: object) -> Any:
    """拒绝明显敏感字段或 Secret 标记，避免外部工具结果进入 Role。"""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or _SENSITIVE_QUERY.search(key):
                raise RuntimeValidationError("EXTERNAL_TOOL_RESULT_SENSITIVE_FIELD")
            result[key] = sanitize_external_result(item)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_external_result(item) for item in value]
    if isinstance(value, str):
        if _SECRET_MARKER.search(value):
            raise RuntimeValidationError("EXTERNAL_TOOL_RESULT_SENSITIVE")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise RuntimeValidationError("EXTERNAL_TOOL_RESULT_INVALID")
