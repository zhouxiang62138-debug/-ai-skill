"""F14-B 两层 Source Fingerprint/Content-Addressed Cache。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import RuntimeValidationError, RuntimeStorageError
from ..session_store import SessionStore
from .store import DerivedRuntimeStore
from .telemetry import RuntimeTelemetry


_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|secret|password|private[_-]?key)"
)


@dataclass(frozen=True)
class SourceReadResult:
    canonical_locator: str
    content_hash: str
    content: bytes
    trusted: bool
    cache_layer: str


class SourceCache:
    """通过 metadata 快速定位，但默认要求可信内容 hash。"""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        project_revision: int,
        policy_hash: str,
        role_scope: str,
        parser_version: str,
        store: SessionStore | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.project_revision = project_revision
        self.policy_hash = policy_hash
        self.role_scope = role_scope
        self.parser_version = parser_version
        self.derived_store = DerivedRuntimeStore(store) if store is not None else None
        self.telemetry = telemetry or RuntimeTelemetry()
        self._fingerprints: dict[str, dict[str, Any]] = {}
        self._contents: dict[str, bytes] = {}

    def _resolve(self, locator: str) -> tuple[Path, str]:
        if not isinstance(locator, str) or not locator or "\x00" in locator:
            raise RuntimeValidationError("F14_CACHE_LOCATOR_INVALID")
        unresolved = self.workspace_root / locator
        if unresolved.is_symlink():
            raise RuntimeValidationError("F14_CACHE_PATH_ANOMALY")
        path = unresolved.resolve()
        try:
            relative = path.relative_to(self.workspace_root)
        except ValueError as exc:
            raise RuntimeValidationError("F14_CACHE_PATH_ANOMALY") from exc
        if not path.is_file():
            raise RuntimeValidationError("F14_CACHE_LOCATOR_STALE")
        return path, relative.as_posix()

    def _fingerprint_key(self, canonical_locator: str) -> str:
        return hashlib.sha256(
            "|".join(
                (
                    canonical_locator,
                    self.role_scope,
                    self.parser_version,
                )
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _file_identity(stat: Any) -> str:
        return f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"

    def _metadata(self, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError as exc:
            raise RuntimeValidationError("F14_CACHE_LOCATOR_STALE") from exc
        return {
            "file_identity": self._file_identity(stat),
            "file_size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "filesystem_metadata": {
                "mode": int(stat.st_mode),
                "ctime_ns": int(getattr(stat, "st_ctime_ns", 0)),
            },
        }

    def _get_fingerprint(self, key: str) -> dict[str, Any] | None:
        if key in self._fingerprints:
            return self._fingerprints[key]
        if self.derived_store is not None:
            value = self.derived_store.get_source_fingerprint(key)
            if value is not None:
                try:
                    value["filesystem_metadata"] = json.loads(
                        value["filesystem_metadata_json"]
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    self.telemetry.record_cache_event(
                        "miss",
                        invalidation_reason="fingerprint_cache_corrupt",
                        fallback_read=True,
                        parser_version=self.parser_version,
                    )
                    return None
                self._fingerprints[key] = value
            return value
        return None

    def _put_fingerprint(
        self,
        key: str,
        canonical_locator: str,
        metadata: Mapping[str, Any],
        content_hash: str,
    ) -> None:
        value = {
            "cache_key": key,
            "canonical_locator": canonical_locator,
            "file_identity": metadata["file_identity"],
            "file_size": metadata["file_size"],
            "mtime_ns": metadata["mtime_ns"],
            "filesystem_metadata": metadata["filesystem_metadata"],
            "project_revision": self.project_revision,
            "policy_hash": self.policy_hash,
            "role_scope": self.role_scope,
            "parser_version": self.parser_version,
            "known_content_hash": content_hash,
        }
        self._fingerprints[key] = value
        if self.derived_store is not None:
            try:
                self.derived_store.put_source_fingerprint(value)
            except Exception:
                self.telemetry.record_cache_event(
                    "miss",
                    invalidation_reason="fingerprint_write_failed",
                    parser_version=self.parser_version,
                )

    def _get_content(self, content_hash: str) -> bytes | None:
        if content_hash in self._contents:
            content = self._contents[content_hash]
            if hashlib.sha256(content).hexdigest() != content_hash:
                self._contents.pop(content_hash, None)
                raise RuntimeStorageError("F14_CONTENT_CACHE_CORRUPT")
            return content
        if self.derived_store is not None:
            value = self.derived_store.get_content_cache(content_hash)
            if value is not None:
                content = bytes(value["content"])
                self._contents[content_hash] = content
                return content
        return None

    def _put_content(self, content_hash: str, content: bytes) -> None:
        self._contents[content_hash] = content
        if self.derived_store is not None:
            try:
                self.derived_store.put_content_cache(content_hash, content)
            except Exception:
                self.telemetry.record_cache_event(
                    "miss",
                    invalidation_reason="content_cache_write_failed",
                    parser_version=self.parser_version,
                )

    @staticmethod
    def _assert_secret_safe(content: bytes) -> None:
        if _SECRET_PATTERN.search(content.decode("utf-8", errors="replace")):
            # Cache 只是读取优化层，Secret 拒绝码必须复用 Context 的公开协议。
            raise RuntimeValidationError("CONTEXT_SECRET_FORBIDDEN")

    def read(
        self,
        locator: str,
        *,
        require_trusted_hash: bool = True,
        protected: bool = False,
        approved: bool = False,
        security_sensitive: bool = False,
    ) -> SourceReadResult:
        path, canonical_locator = self._resolve(locator)
        metadata = self._metadata(path)
        key = self._fingerprint_key(canonical_locator)
        previous = self._get_fingerprint(key)
        must_rehash = require_trusted_hash or protected or approved or security_sensitive
        stable = (
            previous is not None
            and previous.get("canonical_locator") == canonical_locator
            and previous.get("file_identity") == metadata["file_identity"]
            and int(previous.get("file_size", -1)) == metadata["file_size"]
            and int(previous.get("mtime_ns", -1)) == metadata["mtime_ns"]
            and int(previous.get("project_revision", -1)) == self.project_revision
            and previous.get("policy_hash") == self.policy_hash
            and previous.get("role_scope") == self.role_scope
            and previous.get("parser_version") == self.parser_version
        )
        if stable and not must_rehash and previous.get("known_content_hash"):
            try:
                content = self._get_content(str(previous["known_content_hash"]))
            except RuntimeStorageError:
                content = None
                self.telemetry.record_cache_event(
                    "miss",
                    invalidation_reason="content_cache_corrupt",
                    fallback_read=True,
                    parser_version=self.parser_version,
                )
            if content is not None:
                self._assert_secret_safe(content)
                self.telemetry.record_cache_event(
                    "hit", parser_version=self.parser_version
                )
                return SourceReadResult(
                    canonical_locator=canonical_locator,
                    content_hash=str(previous["known_content_hash"]),
                    content=content,
                    trusted=False,
                    cache_layer="fingerprint",
                )
        elif previous is not None:
            self.telemetry.record_cache_event(
                "miss",
                invalidation_reason=(
                    "trusted_rehash_required"
                    if must_rehash
                    else "fingerprint_changed"
                ),
                fallback_read=True,
                parser_version=self.parser_version,
            )
        raw = path.read_bytes()
        self.telemetry.record_file_read(len(raw))
        self._assert_secret_safe(raw)
        content_hash = hashlib.sha256(raw).hexdigest()
        self.telemetry.record_hash()
        self._put_content(content_hash, raw)
        self._put_fingerprint(key, canonical_locator, metadata, content_hash)
        self.telemetry.record_cache_event(
            "miss",
            invalidation_reason="initial_read" if previous is None else "revalidated",
            fallback_read=previous is not None,
            parser_version=self.parser_version,
        )
        return SourceReadResult(
            canonical_locator=canonical_locator,
            content_hash=content_hash,
            content=raw,
            trusted=True,
            cache_layer="content",
        )

    def put_parsed(
        self,
        result: SourceReadResult,
        *,
        parsed: Any,
        derived: Any,
    ) -> None:
        if self.derived_store is not None:
            try:
                self.derived_store.put_parsed_cache(
                    result.content_hash,
                    parser_version=self.parser_version,
                    policy_hash=self.policy_hash,
                    role_scope=self.role_scope,
                    parsed=parsed,
                    derived=derived,
                )
            except Exception:
                self.telemetry.record_cache_event(
                    "miss",
                    invalidation_reason="parsed_cache_write_failed",
                    parser_version=self.parser_version,
                )

    def get_parsed(self, result: SourceReadResult) -> dict[str, Any] | None:
        """按已验证 source hash 读取解析结果；损坏时只返回 Cache Miss。"""

        if self.derived_store is None:
            return None
        try:
            value = self.derived_store.get_parsed_cache(
                result.content_hash,
                parser_version=self.parser_version,
                policy_hash=self.policy_hash,
                role_scope=self.role_scope,
            )
        except RuntimeStorageError:
            self.telemetry.record_cache_event(
                "miss",
                invalidation_reason="parsed_cache_corrupt",
                fallback_read=True,
                parser_version=self.parser_version,
            )
            return None
        if value is None:
            return None
        self.telemetry.record_cache_event("hit", parser_version=self.parser_version)
        return value


__all__ = ["SourceCache", "SourceReadResult"]
