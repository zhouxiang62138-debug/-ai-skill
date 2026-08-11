"""Reference Analysis 统一追加式工件存储。"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from scripts.reference_protocol import (
    assert_valid,
    validate_reference_analysis,
    validate_reference_evidence,
    validate_reference_finding,
    validate_reference_source,
    validate_reference_synthesis,
    validate_versioned_artifact_path,
)
from runtime.execution.path_policy import ExecutionPathPolicy

from .errors import ReferenceAnalysisError, ReferenceArtifactExistsError


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_dump(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _safe_context_identity(value: Any) -> str:
    if not isinstance(value, Mapping):
        return repr(value)
    return "|".join(
        str(value.get(key) or "")
        for key in ("type", "project_id", "change_request_id")
    )


class ReferenceArtifactStore:
    """集中处理 ID、路径、校验、哈希和原子追加写入。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        path_policy: ExecutionPathPolicy | None = None,
        path_actor: str = "reference_analysis",
    ) -> None:
        self.root = Path(project_root).resolve()
        self.path_policy = path_policy or ExecutionPathPolicy()
        if not isinstance(path_actor, str) or not path_actor:
            raise ReferenceAnalysisError("REFERENCE_PATH_ACTOR_INVALID")
        self.path_actor = path_actor

    @staticmethod
    def context_key(context: Mapping[str, Any]) -> str:
        if context.get("type") == "change_request":
            request_id = context.get("change_request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"CR-[0-9]{4}", request_id):
                raise ReferenceAnalysisError("CHANGE_REQUEST_CONTEXT_INVALID")
            return f"change_requests/{request_id}/references"
        if context.get("type") in {"project", "new_project"}:
            return "memory/references"
        raise ReferenceAnalysisError("REFERENCE_CONTEXT_INVALID")

    def _base(self, context: Mapping[str, Any]) -> Path:
        relative = self.context_key(context)
        return self.path_policy.assert_module_path(self.path_actor, self.root, relative, operation="write")

    def _read_path(self, relative: str) -> Path:
        return self.path_policy.assert_module_path(self.path_actor, self.root, relative, operation="read")

    def _write_path(self, relative: str) -> Path:
        return self.path_policy.assert_module_path(self.path_actor, self.root, relative, operation="write")

    def _write_yaml(self, relative: str, value: Mapping[str, Any]) -> dict[str, Any]:
        target = self._write_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ReferenceArtifactExistsError("REFERENCE_ARTIFACT_EXISTS")
        text = _safe_dump(value)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        handle, temporary = tempfile.mkstemp(prefix=".reference-artifact-", dir=str(target.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise ReferenceArtifactExistsError("REFERENCE_ARTIFACT_EXISTS")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": relative.replace("\\", "/"), "sha256": digest, "size_bytes": len(text.encode("utf-8"))}

    def read(self, relative: str) -> dict[str, Any]:
        path = self._read_path(relative)
        if not path.is_file():
            raise ReferenceAnalysisError("REFERENCE_ARTIFACT_NOT_FOUND")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ReferenceAnalysisError("REFERENCE_ARTIFACT_INVALID_YAML") from exc
        if not isinstance(value, dict):
            raise ReferenceAnalysisError("REFERENCE_ARTIFACT_INVALID")
        return value

    def _next_number(self, directory: Path, pattern: str) -> int:
        highest = 0
        if directory.exists():
            for item in directory.iterdir():
                match = re.fullmatch(pattern, item.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1

    def next_reference_id(self, context: Mapping[str, Any]) -> str:
        base = self._base(context)
        return f"REF-{self._next_number(base, r'reference-([0-9]{3})') :03d}"

    def register_source(
        self,
        source: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
        requested_scope: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """注册一条来源，并一次性追加 source/scope 两类元数据。"""

        config_scope = requested_scope or source.get("requested_scope")
        if not isinstance(config_scope, Mapping):
            config_scope = {domain: "unspecified" for domain in (
                "product", "information_architecture", "navigation", "interaction", "layout",
                "visual_style", "components", "design_tokens", "motion", "content_style", "brand",
                "technical_architecture",
            )}
        reference_id = str(source.get("reference_id") or self.next_reference_id(context))
        if re.fullmatch(r"REF-[0-9]{3}", reference_id) is None:
            raise ReferenceAnalysisError("REFERENCE_ID_INVALID")
        locator = source.get("source")
        if isinstance(locator, Mapping):
            for key in ("artifact_ref", "text_ref"):
                value = locator.get(key)
                if value is not None:
                    if not isinstance(value, str) or not value:
                        raise ReferenceAnalysisError("REFERENCE_SOURCE_PATH_INVALID")
                    self._read_path(value)
        base_relative = f"{self.context_key(context)}/reference-{int(reference_id.split('-')[1]):03d}"
        base = self._write_path(base_relative)
        if not base.exists():
            base.mkdir(parents=True, exist_ok=False)
        scope_number = self._next_number(base, r"scope-([0-9]{3})\.yaml")
        scope_id = f"REFSCP-{scope_number:03d}"
        scope_relative = f"{base_relative}/scope-{scope_number:03d}.yaml"
        source_relative = f"{base_relative}/source-001.yaml"
        prepared_scope = {
            "schema_version": 1,
            "scope_id": scope_id,
            "reference_id": reference_id,
            "requested_scope": dict(config_scope),
            "explicit_inclusions": [key for key, value in config_scope.items() if value == "include"],
            "explicit_exclusions": [key for key, value in config_scope.items() if value == "exclude"],
            "source_refs": [reference_id],
            "created_at": _utc_now(),
            "context": dict(context),
            "supersedes": None,
        }
        prepared_source = copy.deepcopy(dict(source))
        prepared_source.update(
            {
                "schema_version": 1,
                "reference_id": reference_id,
                "scope_ref": scope_relative,
                "requested_scope": dict(config_scope),
                "explicit_inclusions": list(prepared_scope["explicit_inclusions"]),
                "explicit_exclusions": list(prepared_scope["explicit_exclusions"]),
                "status": "registered",
                "created_at": str(prepared_source.get("created_at") or _utc_now()),
                "source_origin": prepared_source.get("source_origin") or {"type": "user_provided"},
                "trust_level": "untrusted",
                "context": dict(context),
                "supersedes": None,
            }
        )
        if not isinstance(prepared_source.get("source"), Mapping):
            raise ReferenceAnalysisError("REFERENCE_SOURCE_PAYLOAD_INVALID")
        assert_valid(validate_reference_source(prepared_source), "reference_source")
        # 先检查双目标，避免已存在的历史文件被覆盖。
        if self._write_path(scope_relative).exists() or self._write_path(source_relative).exists():
            raise ReferenceArtifactExistsError("REFERENCE_ARTIFACT_EXISTS")
        self._write_yaml(scope_relative, prepared_scope)
        self._write_yaml(source_relative, prepared_source)
        prepared_source["_artifact_ref"] = source_relative
        prepared_source["_scope_artifact_ref"] = scope_relative
        return prepared_source

    def list_sources(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        roots: list[Path]
        if context is not None:
            roots = [self._base(context)]
        else:
            roots = [self.root / "memory" / "references", self.root / "change_requests"]
        results: list[dict[str, Any]] = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("source-*.yaml")):
                relative = path.relative_to(self.root).as_posix()
                self._read_path(relative)
                record = self.read(relative)
                record["_artifact_ref"] = relative
                results.append(record)
        if include_superseded:
            return results
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for record in results:
            context_key = _safe_context_identity(record.get("context"))
            latest[(context_key, str(record.get("reference_id")))] = record
        return [record for record in latest.values() if record.get("status") != "superseded"]

    def supersede_source(
        self,
        reference_id: str,
        *,
        context: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """追加一份 superseded source，保留原 source 不覆盖、不删除。"""

        if not isinstance(reason, str) or not reason.strip():
            raise ReferenceAnalysisError("REFERENCE_REVOCATION_REASON_REQUIRED")
        sources = [item for item in self.list_sources(context) if str(item.get("reference_id")) == reference_id]
        if len(sources) != 1:
            raise ReferenceAnalysisError("REFERENCE_SOURCE_NOT_ACTIVE")
        previous = sources[0]
        previous_ref = str(previous["_artifact_ref"])
        base_relative = str(Path(previous_ref).parent).replace("\\", "/")
        base = self._write_path(base_relative)
        number = self._next_number(base, r"source-(\d+)\.yaml")
        relative = f"{base_relative}/source-{number:03d}.yaml"
        prepared = copy.deepcopy(previous)
        prepared.pop("_artifact_ref", None)
        prepared.update(
            {
                "status": "superseded",
                "supersedes": previous_ref,
                "created_at": _utc_now(),
            }
        )
        source_payload = prepared.get("source")
        if isinstance(source_payload, Mapping):
            source_payload = copy.deepcopy(dict(source_payload))
            metadata = dict(source_payload.get("metadata") or {})
            metadata["revocation_reason"] = reason
            source_payload["metadata"] = metadata
            prepared["source"] = source_payload
        assert_valid(validate_reference_source(prepared), "reference_source")
        self._write_yaml(relative, prepared)
        prepared["_artifact_ref"] = relative
        return prepared

    def write_evidence_manifest(self, relative: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        values = [dict(item) for item in records]
        for item in values:
            assert_valid(validate_reference_evidence(item), "reference_evidence")
        manifest = {
            "schema_version": 1,
            "manifest_type": "reference_evidence_manifest",
            "reference_id": values[0]["reference_id"] if values else None,
            "evidence": values,
            "created_at": _utc_now(),
            "trust_level": "untrusted",
        }
        return self._write_yaml(relative, manifest)

    def write_analysis(self, relative: str, record: Mapping[str, Any]) -> dict[str, Any]:
        assert_valid(validate_reference_analysis(record), "reference_analysis")
        return self._write_yaml(relative, record)

    def write_finding(self, relative: str, record: Mapping[str, Any]) -> dict[str, Any]:
        assert_valid(validate_reference_finding(record), "reference_finding")
        return self._write_yaml(relative, record)

    def write_synthesis(self, relative: str, record: Mapping[str, Any]) -> dict[str, Any]:
        assert_valid(validate_reference_synthesis(record), "reference_synthesis")
        return self._write_yaml(relative, record)

    def list_syntheses(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if context.get("type") == "change_request":
            base = self.root / self.context_key(context)
            if not base.exists():
                return []
            values = []
            for path in sorted(base.glob("reference-synthesis-*.yaml")):
                relative = path.relative_to(self.root).as_posix()
                self._read_path(relative)
                values.append(self.read(relative) | {"_artifact_ref": relative})
            return values
        base = self.root / "memory" / "references" / "synthesis"
        if not base.exists():
            return []
        values = []
        for path in sorted(base.glob("reference-synthesis-*.yaml")):
            relative = path.relative_to(self.root).as_posix()
            self._read_path(relative)
            values.append(self.read(relative) | {"_artifact_ref": relative})
        return values
