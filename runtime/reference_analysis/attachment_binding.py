"""宿主附件到项目 Reference Evidence 的受控绑定。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.reference_protocol import (
    assert_valid,
    validate_reference_attachment_binding,
    validate_reference_evidence,
)

from .errors import ReferenceAnalysisError


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REF = re.compile(r"^REF-[0-9]{3}$")
_HASH = re.compile(r"^[a-fA-F0-9]{64}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_EVIDENCE_ID = re.compile(r"^REFEV-([0-9]{3})$")
_BINDING_ID = re.compile(r"^REFBND-([0-9]{3})$")


def _root_hash(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _validate_id(value: object, code: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ReferenceAnalysisError(code)
    return value


def _safe_filename(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ReferenceAnalysisError("ATTACHMENT_FILENAME_INVALID")
    if value in {".", ".."} or "/" in value or "\\" in value or Path(value).name != value:
        raise ReferenceAnalysisError("ATTACHMENT_FILENAME_INVALID")
    return value


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if not suffix or not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        return ""
    return suffix


def _assert_not_reparse(path: Path) -> None:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise ReferenceAnalysisError("ATTACHMENT_SOURCE_UNREADABLE") from exc
    if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ReferenceAnalysisError("ATTACHMENT_SOURCE_REPARSE_DENIED")


@dataclass(frozen=True)
class HostAttachment:
    """Host 提供的最小附件元数据，不携带 Cookie、凭据或项目控制面内容。"""

    attachment_id: str
    project_id: str
    project_root_hash: str
    source_path: str | Path
    filename: str
    media_type: str
    expected_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.attachment_id, "ATTACHMENT_ID_INVALID")
        _validate_id(self.project_id, "ATTACHMENT_PROJECT_ID_INVALID")
        if not isinstance(self.project_root_hash, str) or not _HASH.fullmatch(self.project_root_hash):
            raise ReferenceAnalysisError("ATTACHMENT_PROJECT_ROOT_HASH_INVALID")
        if not isinstance(self.source_path, (str, Path)) or not Path(self.source_path).is_absolute():
            raise ReferenceAnalysisError("ATTACHMENT_SOURCE_PATH_INVALID")
        _safe_filename(self.filename)
        if not isinstance(self.media_type, str) or not _MEDIA_TYPE.fullmatch(self.media_type.casefold()):
            raise ReferenceAnalysisError("ATTACHMENT_MEDIA_TYPE_INVALID")
        if self.expected_sha256 is not None and not _HASH.fullmatch(self.expected_sha256):
            raise ReferenceAnalysisError("ATTACHMENT_EXPECTED_HASH_INVALID")


@dataclass(frozen=True)
class AttachmentBindingResult:
    """一次绑定的追加式结果；重试命中相同幂等键时返回相同记录。"""

    binding: Mapping[str, Any]
    evidence: Mapping[str, Any]


class AttachmentBindingStore:
    """把 Host 附件物化到项目 Evidence 区，并保留跨项目绑定约束。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        path_policy: ExecutionPathPolicy | None = None,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.path_policy = path_policy or ExecutionPathPolicy()
        if not self.root.is_dir():
            raise ReferenceAnalysisError("ATTACHMENT_PROJECT_ROOT_INVALID")
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ReferenceAnalysisError("ATTACHMENT_SIZE_LIMIT_INVALID")
        self.max_bytes = max_bytes

    def _path(self, relative: str, *, operation: str) -> Path:
        return self.path_policy.assert_module_path(
            "reference_analysis", self.root, relative, operation=operation
        )

    def _binding_directory(self) -> Path:
        return self._path("artifacts/references/attachment-bindings", operation="write")

    def _records(self) -> list[dict[str, Any]]:
        directory = self._path("artifacts/references/attachment-bindings", operation="read")
        if not directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob("binding-*.yaml")):
            try:
                value = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise ReferenceAnalysisError("ATTACHMENT_BINDING_RECORD_INVALID") from exc
            if not isinstance(value, dict) or not _BINDING_ID.fullmatch(str(value.get("binding_id", ""))):
                raise ReferenceAnalysisError("ATTACHMENT_BINDING_RECORD_INVALID")
            records.append(value)
        return records

    def _next_binding_id(self) -> str:
        highest = 0
        for record in self._records():
            match = _BINDING_ID.fullmatch(str(record["binding_id"]))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"REFBND-{highest + 1:03d}"

    def _next_evidence_id(self) -> str:
        highest = 0
        root = self._path("artifacts/references", operation="read")
        if not root.is_dir():
            return "REFEV-001"
        for path in root.rglob("*.yaml"):
            relative = path.relative_to(self.root).as_posix()
            self._path(relative, operation="read")
            try:
                value = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise ReferenceAnalysisError("ATTACHMENT_EVIDENCE_SCAN_INVALID") from exc
            values: list[Any] = []
            if isinstance(value, Mapping):
                values.append(value.get("evidence_id"))
                evidence = value.get("evidence")
                if isinstance(evidence, list):
                    values.extend(
                        item.get("evidence_id") for item in evidence if isinstance(item, Mapping)
                    )
            for item in values:
                match = _EVIDENCE_ID.fullmatch(str(item or ""))
                if match:
                    highest = max(highest, int(match.group(1)))
        return f"REFEV-{highest + 1:03d}"

    @staticmethod
    def _write_once(path: Path, payload: bytes, *, error_code: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_file() and path.read_bytes() == payload:
                return
            raise ReferenceAnalysisError(error_code)
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                raise ReferenceAnalysisError(error_code)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write_yaml_once(self, relative: str, value: Mapping[str, Any], *, error_code: str) -> None:
        target = self._path(relative, operation="write")
        payload = yaml.safe_dump(dict(value), allow_unicode=True, sort_keys=False).encode("utf-8")
        self._write_once(target, payload, error_code=error_code)

    def bind(
        self,
        attachment: HostAttachment,
        *,
        reference_id: str,
        project_id: str | None = None,
    ) -> AttachmentBindingResult:
        if not _REF.fullmatch(reference_id):
            raise ReferenceAnalysisError("ATTACHMENT_REFERENCE_ID_INVALID")
        target_project_id = project_id or attachment.project_id
        _validate_id(target_project_id, "ATTACHMENT_PROJECT_ID_INVALID")
        if attachment.project_id != target_project_id:
            raise ReferenceAnalysisError("ATTACHMENT_PROJECT_BINDING_INVALID")
        if attachment.project_root_hash.casefold() != _root_hash(self.root):
            raise ReferenceAnalysisError("ATTACHMENT_PROJECT_ROOT_MISMATCH")

        source = Path(attachment.source_path)
        _assert_not_reparse(source)
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise ReferenceAnalysisError("ATTACHMENT_SOURCE_UNREADABLE") from exc
        if not source.is_file():
            raise ReferenceAnalysisError("ATTACHMENT_SOURCE_NOT_FILE")
        try:
            size = source.stat().st_size
            if size > self.max_bytes:
                raise ReferenceAnalysisError("ATTACHMENT_SIZE_LIMIT_EXCEEDED")
            raw = source.read_bytes()
        except OSError as exc:
            raise ReferenceAnalysisError("ATTACHMENT_SOURCE_UNREADABLE") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if attachment.expected_sha256 is not None and attachment.expected_sha256.casefold() != digest:
            raise ReferenceAnalysisError("ATTACHMENT_HASH_MISMATCH")
        idempotency_key = hashlib.sha256(
            "\x1f".join(
                (attachment.project_id, attachment.project_root_hash, reference_id, attachment.attachment_id, digest)
            ).encode("utf-8")
        ).hexdigest()
        for record in self._records():
            if record.get("idempotency_key") == idempotency_key:
                evidence_ref = str(record.get("evidence_ref", ""))
                evidence_path = self._path(evidence_ref, operation="read")
                evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
                if not isinstance(evidence, dict):
                    raise ReferenceAnalysisError("ATTACHMENT_EVIDENCE_RECORD_INVALID")
                artifact_path = self._path(str(record["artifact_ref"]), operation="read")
                if not artifact_path.is_file() or hashlib.sha256(artifact_path.read_bytes()).hexdigest() != digest:
                    raise ReferenceAnalysisError("ATTACHMENT_ARTIFACT_HASH_MISMATCH")
                return AttachmentBindingResult(record, evidence)

        filename = _safe_filename(attachment.filename)
        evidence_id = self._next_evidence_id()
        binding_id = self._next_binding_id()
        reference_directory = f"reference-{int(reference_id.split('-')[1]):03d}"
        artifact_ref = (
            f"artifacts/references/{reference_directory}/evidence/attachments/"
            f"attachment-{digest[:32]}{_safe_suffix(filename)}"
        )
        evidence_ref = f"artifacts/references/{reference_directory}/evidence/evidence-{int(evidence_id.split('-')[1]):03d}.yaml"
        binding_ref = f"artifacts/references/attachment-bindings/binding-{int(binding_id.split('-')[1]):03d}.yaml"

        evidence = {
            "schema_version": 1,
            "evidence_id": evidence_id,
            "reference_id": reference_id,
            "evidence_type": "image" if attachment.media_type.casefold().startswith("image/") else "file",
            "artifact_ref": artifact_ref,
            "integrity": {"algorithm": "sha256", "sha256": digest},
            "viewport": None,
            "captured_at": None,
            "source_location": {"type": "host_attachment", "attachment_id": attachment.attachment_id},
            "locator": f"attachment:{attachment.attachment_id}",
            "metadata": {
                "filename": filename,
                "media_type": attachment.media_type.casefold(),
                "size_bytes": len(raw),
                "project_id": attachment.project_id,
                "project_root_hash": attachment.project_root_hash,
            },
            "trust_level": "untrusted",
        }
        assert_valid(validate_reference_evidence(evidence), "reference_evidence")
        binding = {
            "schema_version": 1,
            "binding_type": "host_attachment_reference_evidence",
            "binding_id": binding_id,
            "idempotency_key": idempotency_key,
            "attachment_id": attachment.attachment_id,
            "project_id": attachment.project_id,
            "project_root_hash": attachment.project_root_hash,
            "reference_id": reference_id,
            "evidence_id": evidence_id,
            "artifact_ref": artifact_ref,
            "evidence_ref": evidence_ref,
            "sha256": digest,
            "size_bytes": len(raw),
            "media_type": attachment.media_type.casefold(),
            "trust_level": "untrusted",
        }
        self._write_once(
            self._path(artifact_ref, operation="write"),
            raw,
            error_code="ATTACHMENT_ARTIFACT_EXISTS",
        )
        self._write_yaml_once(
            evidence_ref, evidence, error_code="ATTACHMENT_EVIDENCE_EXISTS"
        )
        assert_valid(
            validate_reference_attachment_binding(binding),
            "reference_attachment_binding",
        )
        self._write_yaml_once(binding_ref, binding, error_code="ATTACHMENT_BINDING_EXISTS")
        return AttachmentBindingResult(binding, evidence)
