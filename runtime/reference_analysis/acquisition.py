"""RA7-B Acquisition 核心协议、生命周期和安全边界。

本模块只负责受控的输入采集元数据，不负责产品决策、Runtime 状态提交或
模型调用。原始内容始终留在项目内受控工件中，Manifest 只保存摘要、哈希和
可审计引用。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import yaml

from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.reference_protocol import assert_valid, validate_acquisition_manifest

from .errors import ReferenceAnalysisError


class ProviderAvailability(StrEnum):
    """Provider 的声明能力和当前环境能力必须分开记录。"""

    SUPPORTED = "SUPPORTED"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED_BY_ENVIRONMENT = "BLOCKED_BY_ENVIRONMENT"


class AcquisitionStatus(StrEnum):
    """统一的 Acquisition Tool Call 生命周期。"""

    REQUESTED = "REQUESTED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    UNKNOWN_AFTER_CRASH = "UNKNOWN_AFTER_CRASH"


_ACQUISITION_ID = re.compile(r"^ACQ-[0-9]{6}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SENSITIVE_QUERY = re.compile(
    r"(?:token|password|passwd|secret|api[_-]?key|authorization|credential|cookie)",
    re.IGNORECASE,
)
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_value(value: Any) -> Any:
    """把请求元数据转成稳定结构，拒绝把原始内容放进指纹。"""

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key).casefold() not in {"content", "raw", "bytes", "image_data", "html"}
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ReferenceAnalysisError("ACQUISITION_FINGERPRINT_INPUT_INVALID")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_identifier(value: str, code: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ReferenceAnalysisError(code)
    return value


@dataclass(frozen=True)
class AcquisitionRequest:
    """一次受控采集请求，只携带 locator 和范围，不携带原始内容。"""

    reference_id: str
    source_type: str
    locator: Mapping[str, Any]
    context: Mapping[str, Any]
    requested_scope: Mapping[str, str] = field(default_factory=dict)
    provider_id: str | None = None
    provider_version: int | None = None
    snapshot_version: int = 1
    refresh_of: str | None = None
    request_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str) or re.fullmatch(r"REF-[0-9]{3}", self.reference_id) is None:
            raise ReferenceAnalysisError("ACQUISITION_REFERENCE_ID_INVALID")
        _validate_identifier(self.source_type, "ACQUISITION_SOURCE_TYPE_INVALID")
        if not isinstance(self.locator, Mapping) or not self.locator:
            raise ReferenceAnalysisError("ACQUISITION_LOCATOR_INVALID")
        if not isinstance(self.context, Mapping) or not self.context.get("project_id"):
            raise ReferenceAnalysisError("ACQUISITION_CONTEXT_INVALID")
        if not isinstance(self.requested_scope, Mapping):
            raise ReferenceAnalysisError("ACQUISITION_SCOPE_INVALID")
        if not isinstance(self.snapshot_version, int) or self.snapshot_version < 1:
            raise ReferenceAnalysisError("ACQUISITION_SNAPSHOT_VERSION_INVALID")
        if self.refresh_of is not None and not _ACQUISITION_ID.fullmatch(self.refresh_of):
            raise ReferenceAnalysisError("ACQUISITION_REFRESH_REFERENCE_INVALID")
        if self.provider_id is not None:
            _validate_identifier(self.provider_id, "ACQUISITION_PROVIDER_ID_INVALID")
        if self.provider_version is not None and (
            not isinstance(self.provider_version, int) or self.provider_version < 1
        ):
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_VERSION_INVALID")
        object.__setattr__(
            self,
            "request_fingerprint",
            _fingerprint(
                {
                    "reference_id": self.reference_id,
                    "source_type": self.source_type,
                    "locator": self.locator,
                    "context": self.context,
                    "requested_scope": self.requested_scope,
                    "provider_id": self.provider_id,
                    "provider_version": self.provider_version,
                    "snapshot_version": self.snapshot_version,
                }
            ),
        )

    def refresh(self, *, refresh_of: str) -> "AcquisitionRequest":
        """刷新产生新快照版本；它和 Retry 不是同一个身份。"""

        if not _ACQUISITION_ID.fullmatch(refresh_of):
            raise ReferenceAnalysisError("ACQUISITION_REFRESH_REFERENCE_INVALID")
        return replace(
            self,
            snapshot_version=self.snapshot_version + 1,
            refresh_of=refresh_of,
        )


@dataclass(frozen=True)
class ProviderCapability:
    """Provider 的静态能力与当前可用性描述。"""

    provider_id: str
    version: int
    source_types: tuple[str, ...]
    availability: ProviderAvailability
    capabilities: tuple[str, ...] = ()
    deterministic: bool = True
    requires_network: bool = False
    supports_retry: bool = True
    supports_refresh: bool = True

    def __post_init__(self) -> None:
        _validate_identifier(self.provider_id, "ACQUISITION_PROVIDER_ID_INVALID")
        if not isinstance(self.version, int) or self.version < 1:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_VERSION_INVALID")
        if not self.source_types or not all(
            isinstance(item, str) and _SAFE_ID.fullmatch(item) for item in self.source_types
        ):
            raise ReferenceAnalysisError("ACQUISITION_SOURCE_TYPES_INVALID")
        object.__setattr__(self, "availability", ProviderAvailability(self.availability))

    def supports(self, source_type: str) -> bool:
        return source_type in self.source_types


@dataclass(frozen=True)
class AcquiredArtifact:
    """采集器输出的不可变工件摘要。"""

    artifact_ref: str
    sha256: str
    size_bytes: int
    media_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_ref, str) or not self.artifact_ref:
            raise ReferenceAnalysisError("ACQUISITION_ARTIFACT_REF_INVALID")
        if not _SHA256.fullmatch(self.sha256):
            raise ReferenceAnalysisError("ACQUISITION_ARTIFACT_HASH_INVALID")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ReferenceAnalysisError("ACQUISITION_ARTIFACT_SIZE_INVALID")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ReferenceAnalysisError("ACQUISITION_ARTIFACT_MEDIA_TYPE_INVALID")


@dataclass(frozen=True)
class AcquisitionOutput:
    """Provider 的结构化结果；Provider 不能输出 Runtime 权限字段。"""

    artifacts: tuple[AcquiredArtifact, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    evidence_type: str | None = None

    def __post_init__(self) -> None:
        if not self.artifacts:
            raise ReferenceAnalysisError("ACQUISITION_OUTPUT_EMPTY")


class AcquisitionProvider(Protocol):
    """受控 Acquisition Provider 接口，不等同于 Agent。"""

    capability: ProviderCapability

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        root: Path,
        path_policy: ExecutionPathPolicy,
    ) -> AcquisitionOutput:
        ...


class AcquisitionProviderRegistry:
    """按配置/显式注册 Provider，并对 unavailable 能力 fail closed。"""

    def __init__(self, providers: Sequence[AcquisitionProvider] = ()) -> None:
        self._providers: dict[str, AcquisitionProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: AcquisitionProvider) -> None:
        capability = provider.capability
        if capability.provider_id in self._providers:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_DUPLICATE")
        self._providers[capability.provider_id] = provider

    def provider(self, provider_id: str, *, require_available: bool = True) -> AcquisitionProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_NOT_REGISTERED")
        availability = provider.capability.availability
        if require_available and availability is not ProviderAvailability.AVAILABLE:
            raise ReferenceAnalysisError(
                "ACQUISITION_PROVIDER_BLOCKED"
                if availability is ProviderAvailability.BLOCKED_BY_ENVIRONMENT
                else "ACQUISITION_PROVIDER_UNAVAILABLE"
            )
        return provider

    def for_source(self, source_type: str, *, require_available: bool = True) -> AcquisitionProvider:
        matches = [
            provider
            for provider in self._providers.values()
            if provider.capability.supports(source_type)
        ]
        if not matches:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_NOT_REGISTERED")
        matches.sort(key=lambda item: item.capability.provider_id)
        provider = matches[0]
        if require_available and provider.capability.availability is not ProviderAvailability.AVAILABLE:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_UNAVAILABLE")
        return provider

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return tuple(provider.capability for provider in self._providers.values())


_TRANSITIONS: dict[AcquisitionStatus, frozenset[AcquisitionStatus]] = {
    AcquisitionStatus.REQUESTED: frozenset({AcquisitionStatus.STARTED, AcquisitionStatus.CANCELLED}),
    AcquisitionStatus.STARTED: frozenset(
        {
            AcquisitionStatus.SUCCEEDED,
            AcquisitionStatus.FAILED,
            AcquisitionStatus.TIMED_OUT,
            AcquisitionStatus.CANCELLED,
            AcquisitionStatus.UNKNOWN_AFTER_CRASH,
        }
    ),
    AcquisitionStatus.FAILED: frozenset({AcquisitionStatus.REQUESTED}),
    AcquisitionStatus.TIMED_OUT: frozenset({AcquisitionStatus.REQUESTED}),
    AcquisitionStatus.CANCELLED: frozenset({AcquisitionStatus.REQUESTED}),
    AcquisitionStatus.UNKNOWN_AFTER_CRASH: frozenset({AcquisitionStatus.REQUESTED}),
    AcquisitionStatus.SUCCEEDED: frozenset(),
}


@dataclass(frozen=True)
class AcquisitionManifest:
    """一个 Acquisition identity 的当前不可变快照。"""

    acquisition_id: str
    reference_id: str
    source_type: str
    request_fingerprint: str
    provider_id: str
    provider_version: int
    snapshot_version: int
    status: AcquisitionStatus
    attempt: int = 1
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error_code: str | None = None
    refresh_of: str | None = None
    history: tuple[str, ...] = (AcquisitionStatus.REQUESTED.value,)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not _ACQUISITION_ID.fullmatch(self.acquisition_id):
            raise ReferenceAnalysisError("ACQUISITION_ID_INVALID")
        if re.fullmatch(r"REF-[0-9]{3}", self.reference_id) is None:
            raise ReferenceAnalysisError("ACQUISITION_REFERENCE_ID_INVALID")
        _validate_identifier(self.source_type, "ACQUISITION_SOURCE_TYPE_INVALID")
        if not _SHA256.fullmatch(self.request_fingerprint):
            raise ReferenceAnalysisError("ACQUISITION_FINGERPRINT_INVALID")
        _validate_identifier(self.provider_id, "ACQUISITION_PROVIDER_ID_INVALID")
        if not isinstance(self.provider_version, int) or self.provider_version < 1:
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_VERSION_INVALID")
        if not isinstance(self.snapshot_version, int) or self.snapshot_version < 1:
            raise ReferenceAnalysisError("ACQUISITION_SNAPSHOT_VERSION_INVALID")
        if not isinstance(self.attempt, int) or self.attempt < 1:
            raise ReferenceAnalysisError("ACQUISITION_ATTEMPT_INVALID")
        object.__setattr__(self, "status", AcquisitionStatus(self.status))
        if self.refresh_of is not None and not _ACQUISITION_ID.fullmatch(self.refresh_of):
            raise ReferenceAnalysisError("ACQUISITION_REFRESH_REFERENCE_INVALID")

    def transition(self, status: AcquisitionStatus, **changes: Any) -> "AcquisitionManifest":
        target = AcquisitionStatus(status)
        if target is not self.status and target not in _TRANSITIONS[self.status]:
            raise ReferenceAnalysisError("ACQUISITION_INVALID_TRANSITION")
        if target is AcquisitionStatus.REQUESTED and self.status is not AcquisitionStatus.REQUESTED:
            changes.setdefault("attempt", self.attempt + 1)
        history = tuple(self.history)
        if not history or history[-1] != target.value:
            history = history + (target.value,)
        return replace(
            self,
            status=target,
            history=history,
            updated_at=_utc_now(),
            **changes,
        )

    def to_record(self, *, artifact_ref: str | None = None) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "manifest_type": "acquisition_manifest",
            "acquisition_id": self.acquisition_id,
            "reference_id": self.reference_id,
            "source_type": self.source_type,
            "request_fingerprint": self.request_fingerprint,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "snapshot_version": self.snapshot_version,
            "status": self.status.value,
            "attempt": self.attempt,
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
            "error_code": self.error_code,
            "refresh_of": self.refresh_of,
            "history": list(self.history),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "trust_level": "untrusted",
        }
        if artifact_ref is not None:
            record["manifest_artifact_ref"] = artifact_ref
        return record


class AcquisitionManifestStore:
    """追加式保存 Acquisition Manifest，绝不覆盖旧快照。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        path_policy: ExecutionPathPolicy | None = None,
        relative_root: str = "artifacts/references/acquisitions",
    ) -> None:
        self.root = Path(project_root).resolve()
        self.path_policy = path_policy or ExecutionPathPolicy()
        self.relative_root = relative_root.replace("\\", "/").strip("/")

    def _directory(self, *, write: bool) -> Path:
        return self.path_policy.assert_module_path(
            "reference_analysis", self.root, self.relative_root, operation="write" if write else "read"
        )

    def _next_number(self, directory: Path) -> int:
        highest = 0
        for path in directory.glob("manifest-*.yaml") if directory.exists() else ():
            match = re.fullmatch(r"manifest-([0-9]{6})\.yaml", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def _append(self, manifest: AcquisitionManifest) -> dict[str, Any]:
        directory = self._directory(write=True)
        directory.mkdir(parents=True, exist_ok=True)
        number = self._next_number(directory)
        relative = f"{self.relative_root}/manifest-{number:06d}.yaml"
        target = self.path_policy.assert_module_path(
            "reference_analysis", self.root, relative, operation="write"
        )
        record = manifest.to_record(artifact_ref=relative)
        assert_valid(validate_acquisition_manifest(record), "acquisition_manifest")
        text = yaml.safe_dump(record, allow_unicode=True, sort_keys=False)
        handle, temporary = tempfile.mkstemp(prefix=".acquisition-manifest-", dir=str(directory))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise ReferenceAnalysisError("ACQUISITION_MANIFEST_EXISTS")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return record

    def _records(self) -> list[dict[str, Any]]:
        directory = self._directory(write=False)
        if not directory.exists():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(directory.glob("manifest-*.yaml")):
            relative = path.relative_to(self.root).as_posix()
            self.path_policy.assert_module_path("reference_analysis", self.root, relative, operation="read")
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("manifest_type") == "acquisition_manifest":
                values.append(value)
        return values

    @staticmethod
    def _from_record(record: Mapping[str, Any]) -> AcquisitionManifest:
        return AcquisitionManifest(
            acquisition_id=str(record["acquisition_id"]),
            reference_id=str(record["reference_id"]),
            source_type=str(record["source_type"]),
            request_fingerprint=str(record["request_fingerprint"]),
            provider_id=str(record["provider_id"]),
            provider_version=int(record["provider_version"]),
            snapshot_version=int(record["snapshot_version"]),
            status=AcquisitionStatus(str(record["status"])),
            attempt=int(record.get("attempt", 1)),
            artifact_refs=tuple(str(item) for item in record.get("artifact_refs", [])),
            evidence_refs=tuple(str(item) for item in record.get("evidence_refs", [])),
            error_code=record.get("error_code"),
            refresh_of=record.get("refresh_of"),
            history=tuple(str(item) for item in record.get("history", [])),
            created_at=str(record.get("created_at") or _utc_now()),
            updated_at=str(record.get("updated_at") or _utc_now()),
        )

    def latest(self, acquisition_id: str) -> AcquisitionManifest | None:
        records = [item for item in self._records() if item.get("acquisition_id") == acquisition_id]
        return self._from_record(records[-1]) if records else None

    def find_identity(self, request_fingerprint: str, snapshot_version: int = 1) -> AcquisitionManifest | None:
        records = [
            item
            for item in self._records()
            if item.get("request_fingerprint") == request_fingerprint
            and int(item.get("snapshot_version", 1)) == snapshot_version
        ]
        return self._from_record(records[-1]) if records else None

    def begin(self, request: AcquisitionRequest, capability: ProviderCapability) -> AcquisitionManifest:
        if not capability.supports(request.source_type):
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_SOURCE_UNSUPPORTED")
        existing = self.find_identity(request.request_fingerprint, request.snapshot_version)
        if existing is not None:
            return existing
        existing_ids = [
            int(item["acquisition_id"].split("-")[1])
            for item in self._records()
            if isinstance(item.get("acquisition_id"), str) and _ACQUISITION_ID.fullmatch(item["acquisition_id"])
        ]
        acquisition_id = f"ACQ-{(max(existing_ids, default=0) + 1):06d}"
        manifest = AcquisitionManifest(
            acquisition_id=acquisition_id,
            reference_id=request.reference_id,
            source_type=request.source_type,
            request_fingerprint=request.request_fingerprint,
            provider_id=capability.provider_id,
            provider_version=capability.version,
            snapshot_version=request.snapshot_version,
            status=AcquisitionStatus.REQUESTED,
            refresh_of=request.refresh_of,
        )
        self._append(manifest)
        return manifest

    def transition(self, manifest: AcquisitionManifest, status: AcquisitionStatus, **changes: Any) -> AcquisitionManifest:
        updated = manifest.transition(status, **changes)
        self._append(updated)
        return updated

    def retry(self, manifest: AcquisitionManifest) -> AcquisitionManifest:
        if manifest.status is AcquisitionStatus.SUCCEEDED:
            return manifest
        return self.transition(manifest, AcquisitionStatus.REQUESTED)

    def refresh(self, request: AcquisitionRequest, previous: AcquisitionManifest, capability: ProviderCapability) -> AcquisitionManifest:
        refreshed = request.refresh(refresh_of=previous.acquisition_id)
        return self.begin(refreshed, capability)

    def recover_started(self) -> tuple[AcquisitionManifest, ...]:
        recovered: list[AcquisitionManifest] = []
        latest_by_id: dict[str, AcquisitionManifest] = {}
        for record in self._records():
            manifest = self._from_record(record)
            latest_by_id[manifest.acquisition_id] = manifest
        for manifest in latest_by_id.values():
            if manifest.status is AcquisitionStatus.STARTED:
                recovered.append(
                    self.transition(
                        manifest,
                        AcquisitionStatus.UNKNOWN_AFTER_CRASH,
                        error_code="ACQUISITION_CRASH_RECOVERY_REQUIRED",
                    )
                )
        return tuple(recovered)


@dataclass(frozen=True)
class BrowserIsolationContract:
    """Reference Browser 的隔离要求，供后续 Provider 复用。"""

    clean_profile: bool = True
    host_cookies: bool = False
    saved_passwords: bool = False
    extensions: bool = False
    isolated_storage: bool = True
    downloads: bool = False
    popups: bool = False

    def validate(self) -> None:
        if not self.clean_profile or self.host_cookies or self.saved_passwords or self.extensions:
            raise ReferenceAnalysisError("BROWSER_ISOLATION_PROFILE_INVALID")
        if not self.isolated_storage or self.downloads or self.popups:
            raise ReferenceAnalysisError("BROWSER_ISOLATION_POLICY_INVALID")


def _normalize_host(host: str) -> str:
    return host.casefold().rstrip(".")


def _assert_public_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ReferenceAnalysisError("WEB_PRIVATE_ADDRESS_REJECTED")


def validate_resolved_addresses(addresses: Sequence[str]) -> tuple[str, ...]:
    """校验 DNS 解析结果，防止域名解析到内网或元数据地址。"""

    if not addresses:
        raise ReferenceAnalysisError("WEB_DNS_RESOLUTION_EMPTY")
    normalized: list[str] = []
    for item in addresses:
        if not isinstance(item, str):
            raise ReferenceAnalysisError("WEB_DNS_RESULT_INVALID")
        _assert_public_ip(item)
        normalized.append(item)
    return tuple(dict.fromkeys(normalized))


def resolve_public_addresses(host: str, *, resolver: Callable[..., Any] | None = None) -> tuple[str, ...]:
    """使用可注入 resolver 做 DNS 安全检查；默认不主动发起网络请求。"""

    if resolver is None:
        raise ReferenceAnalysisError("WEB_DNS_RESOLUTION_NOT_AUTHORIZED")
    try:
        results = resolver(host, 443, type=socket.SOCK_STREAM)
    except TypeError:
        results = resolver(host)
    except OSError as exc:
        raise ReferenceAnalysisError("WEB_DNS_RESOLUTION_FAILED") from exc
    addresses = []
    for item in results:
        if isinstance(item, tuple) and len(item) >= 5:
            addresses.append(str(item[4][0]))
        elif isinstance(item, str):
            addresses.append(item)
    return validate_resolved_addresses(addresses)


def normalize_public_url(
    url: str,
    *,
    allowed_schemes: Sequence[str] = ("https",),
    resolver: Callable[..., Any] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """规范化一个 URL，并在提供 resolver 时执行 DNS 解析安全校验。"""

    if not isinstance(url, str) or not url or len(url) > 4096 or "\x00" in url:
        raise ReferenceAnalysisError("WEB_URL_INVALID")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ReferenceAnalysisError("WEB_URL_INVALID") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {str(item).casefold() for item in allowed_schemes}:
        raise ReferenceAnalysisError("WEB_UNSAFE_SCHEME")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ReferenceAnalysisError("WEB_URL_INVALID")
    host = _normalize_host(parsed.hostname)
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise ReferenceAnalysisError("WEB_HOST_REJECTED")
    _assert_public_ip(host)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE_QUERY.search(key) or _SENSITIVE_QUERY.search(value):
            raise ReferenceAnalysisError("WEB_SENSITIVE_QUERY_REJECTED")
    if port is not None and not (1 <= port <= 65535):
        raise ReferenceAnalysisError("WEB_PORT_INVALID")
    resolved = resolve_public_addresses(host, resolver=resolver) if resolver is not None else ()
    canonical = urlunsplit((scheme, host, parsed.path or "/", parsed.query, ""))
    return canonical, resolved


def validate_redirect_chain(
    urls: Sequence[str],
    *,
    max_redirects: int = 5,
    resolver: Callable[..., Any] | None = None,
) -> tuple[str, ...]:
    """每一次 redirect 都重新执行 URL 和 DNS 安全检查。"""

    if not isinstance(max_redirects, int) or max_redirects < 0:
        raise ReferenceAnalysisError("WEB_REDIRECT_LIMIT_INVALID")
    if len(urls) == 0 or len(urls) - 1 > max_redirects:
        raise ReferenceAnalysisError("WEB_REDIRECT_LIMIT_EXCEEDED")
    canonical: list[str] = []
    for url in urls:
        normalized, _ = normalize_public_url(url, resolver=resolver)
        if canonical and urlsplit(normalized).scheme != urlsplit(canonical[-1]).scheme:
            raise ReferenceAnalysisError("WEB_REDIRECT_SCHEME_DOWNGRADE")
        canonical.append(normalized)
    return tuple(canonical)


def deny_download(*, operation: str = "download") -> None:
    """RA7-B 不允许 Provider 通过隐藏下载扩大采集范围。"""

    if operation.casefold() in {"download", "save_as", "attachment"}:
        raise ReferenceAnalysisError("WEB_DOWNLOAD_DENIED")


def deny_credential_access() -> None:
    """Reference Acquisition 永远不能读取 Host cookies/passwords。"""

    raise ReferenceAnalysisError("WEB_CREDENTIAL_ACCESS_DENIED")


def image_dimensions(raw: bytes, suffix: str) -> tuple[int, int] | None:
    """读取常见图片头部尺寸，不解码像素，避免压缩炸弹。"""

    suffix = suffix.casefold()
    if suffix == ".png" and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if suffix in {".jpg", ".jpeg"} and raw.startswith(b"\xff\xd8\xff"):
        index = 2
        while index + 9 < len(raw):
            if raw[index] != 0xFF:
                index += 1
                continue
            marker = raw[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(raw):
                break
            segment_length = int.from_bytes(raw[index : index + 2], "big")
            if segment_length < 2 or index + segment_length > len(raw):
                break
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                if segment_length >= 7:
                    return int.from_bytes(raw[index + 5 : index + 7], "big"), int.from_bytes(raw[index + 3 : index + 5], "big")
            index += segment_length
    if suffix == ".webp" and len(raw) >= 30 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        if raw[12:16] == b"VP8X":
            width = 1 + int.from_bytes(raw[24:27], "little")
            height = 1 + int.from_bytes(raw[27:30], "little")
            return width, height
    return None


class LocalImageAcquisitionProvider:
    """项目内图片的安全、确定性 Acquisition Provider。"""

    capability = ProviderCapability(
        provider_id="local-image-acquisition",
        version=1,
        source_types=("image",),
        availability=ProviderAvailability.AVAILABLE,
        capabilities=("safe_path", "format", "size", "dimensions", "hash", "metadata"),
        deterministic=True,
    )

    def __init__(self, *, max_bytes: int = 10 * 1024 * 1024, max_pixels: int = 100_000_000) -> None:
        if max_bytes < 1 or max_pixels < 1:
            raise ReferenceAnalysisError("IMAGE_LIMIT_INVALID")
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        root: Path,
        path_policy: ExecutionPathPolicy,
    ) -> AcquisitionOutput:
        if request.source_type != "image":
            raise ReferenceAnalysisError("ACQUISITION_PROVIDER_SOURCE_UNSUPPORTED")
        artifact_ref = request.locator.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise ReferenceAnalysisError("IMAGE_ARTIFACT_REQUIRED")
        path = path_policy.assert_module_path("reference_analysis", root, artifact_ref, operation="read")
        if not path.is_file():
            raise ReferenceAnalysisError("IMAGE_ARTIFACT_NOT_FOUND")
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ReferenceAnalysisError("IMAGE_TOO_LARGE")
        suffix = path.suffix.casefold()
        magic = {
            ".png": b"\x89PNG\r\n\x1a\n",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
            ".webp": b"RIFF",
        }
        if suffix not in magic:
            raise ReferenceAnalysisError("IMAGE_FORMAT_UNSUPPORTED")
        raw = path.read_bytes()
        valid = raw.startswith(magic[suffix]) and (
            suffix != ".webp" or len(raw) >= 12 and raw[8:12] == b"WEBP"
        )
        if not valid:
            raise ReferenceAnalysisError("IMAGE_INTEGRITY_INVALID")
        digest = hashlib.sha256(raw).hexdigest()
        expected_hash = request.locator.get("content_hash")
        if expected_hash is not None and str(expected_hash).casefold() != digest:
            raise ReferenceAnalysisError("IMAGE_SOURCE_HASH_MISMATCH")
        expected_size = request.locator.get("size_bytes")
        if expected_size is not None and int(expected_size) != size:
            raise ReferenceAnalysisError("IMAGE_SOURCE_SIZE_MISMATCH")
        dimensions = image_dimensions(raw, suffix)
        if dimensions and dimensions[0] * dimensions[1] > self.max_pixels:
            raise ReferenceAnalysisError("IMAGE_PIXEL_BUDGET_EXCEEDED")
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }[suffix]
        metadata: dict[str, Any] = {"size_bytes": size, "format": suffix[1:]}
        if dimensions:
            metadata.update({"width": dimensions[0], "height": dimensions[1], "pixel_count": dimensions[0] * dimensions[1]})
        return AcquisitionOutput(
            artifacts=(AcquiredArtifact(artifact_ref, digest, size, media_type, metadata),),
            metadata={"source_type": "image", "trust_level": "untrusted"},
            evidence_type="image",
        )


def manifest_to_evidence(
    manifest: AcquisitionManifest,
    artifact: AcquiredArtifact,
    *,
    locator: str | None = None,
) -> dict[str, Any]:
    """把 Acquisition 结果转换成现有 Reference Evidence v1 的扩展字段。"""

    return {
        "schema_version": 1,
        "evidence_id": None,
        "reference_id": manifest.reference_id,
        "evidence_type": "image" if manifest.source_type == "image" else "file",
        "artifact_ref": artifact.artifact_ref,
        "integrity": {"algorithm": "sha256", "sha256": artifact.sha256},
        "viewport": None,
        "captured_at": manifest.updated_at,
        "source_location": None,
        "locator": locator or artifact.artifact_ref,
        "metadata": dict(artifact.metadata),
        "acquisition_id": manifest.acquisition_id,
        "acquisition_status": manifest.status.value,
        "request_fingerprint": manifest.request_fingerprint,
        "provider_id": manifest.provider_id,
        "provider_version": manifest.provider_version,
        "snapshot_version": manifest.snapshot_version,
        "trust_level": "untrusted",
    }
