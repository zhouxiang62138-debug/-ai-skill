"""Host-side Workspace Snapshot / Restore 服务。

该模块只处理受控文件存储，不连接 SessionStore、SQLite 或 Lease。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid
from typing import Any, Iterable

from runtime.errors import RuntimeStorageError, RuntimeValidationError

from .models import ExecutionContext
from .path_policy import ExecutionPathPolicy


_SECRET_CONTENT = re.compile(
    rb"(?i)(?:token|secret|password|api[_-]?key|credential)\s*[:=]"
)
_SENSITIVE_NAME = re.compile(
    r"(?i)(?:token|secret|password|api[_-]?key|credential)"
)
_CONTROL_PLANE_NAMES = frozenset(
    {
        ".runtime",
        "project.yaml",
        "sessions.sqlite3",
        "tool-results",
        "checkpoints",
        "locks",
    }
)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeStorageError("SNAPSHOT_MANIFEST_INVALID") from exc


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise RuntimeStorageError("SNAPSHOT_FILE_READ_FAILED") from exc
    return digest.hexdigest(), size


def _workspace_hash(entries: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda value: str(value["path"])):
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _path_is_safe(relative: str) -> bool:
    if not isinstance(relative, str) or not relative:
        return False
    normalized = relative.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "." not in path.parts
        and bool(path.parts)
    )


class WorkspaceSnapshotService:
    """在当前 workspace 外保存不可覆盖 Snapshot，并执行受控恢复。"""

    def __init__(
        self,
        snapshot_root: str | Path,
        *,
        path_policy: ExecutionPathPolicy | None = None,
    ) -> None:
        self.root = Path(snapshot_root).resolve()
        self._path_policy = path_policy or ExecutionPathPolicy()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _root_hash(root: Path) -> str:
        return hashlib.sha256(str(root).encode("utf-8")).hexdigest()

    @staticmethod
    def _is_control_plane(relative: str) -> bool:
        parts = {part.casefold() for part in PurePosixPath(relative).parts}
        return bool(parts & _CONTROL_PLANE_NAMES)

    @staticmethod
    def _is_sensitive(relative: str) -> bool:
        return any(_SENSITIVE_NAME.search(part) for part in PurePosixPath(relative).parts)

    def _assert_storage_outside_workspace(self, workspace: Path) -> None:
        try:
            self.root.relative_to(workspace)
        except ValueError:
            return
        raise RuntimeValidationError("SNAPSHOT_STORAGE_MUST_BE_OUTSIDE_WORKSPACE")

    def _iter_workspace_files(self, context: ExecutionContext, root: Path) -> list[tuple[str, Path]]:
        self._assert_storage_outside_workspace(root)
        if not root.is_dir():
            raise RuntimeValidationError("project_root 必须是已存在的目录")
        files: list[tuple[str, Path]] = []
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            if self._is_control_plane(relative) or self._is_sensitive(relative):
                continue
            try:
                resolved = self._path_policy.assert_path(
                    context.role, str(root), relative, operation="read"
                )
            except RuntimeValidationError:
                continue
            if resolved.is_file():
                files.append((relative, resolved))
        return sorted(files, key=lambda item: item[0])

    @staticmethod
    def _copy_file_without_secrets(source: Path, target: Path) -> tuple[str, int] | None:
        digest = hashlib.sha256()
        size = 0
        previous = b""
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as source_handle, target.open("wb") as target_handle:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    window = previous + chunk
                    if _SECRET_CONTENT.search(window):
                        target_handle.close()
                        target.unlink(missing_ok=True)
                        return None
                    previous = window[-256:]
                    target_handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise RuntimeStorageError("SNAPSHOT_FILE_COPY_FAILED") from exc
        return digest.hexdigest(), size

    def create(self, context: ExecutionContext) -> str:
        """创建不可覆盖 Snapshot，并在发布前完成内容 hash 校验。"""

        workspace = Path(context.project_root).resolve()
        files = self._iter_workspace_files(context, workspace)
        snapshot_id = f"snapshot-{uuid.uuid4().hex}"
        temporary = Path(
            tempfile.mkdtemp(prefix=".snapshot-create-", dir=str(self.root))
        )
        staged_files = temporary / "files"
        entries: list[dict[str, Any]] = []
        try:
            for relative, source in files:
                copied = self._copy_file_without_secrets(
                    source, staged_files / Path(*PurePosixPath(relative).parts)
                )
                if copied is None:
                    continue
                file_hash, size = copied
                entries.append({"path": relative, "size": size, "sha256": file_hash})

            entries.sort(key=lambda value: str(value["path"]))
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "snapshot_id": snapshot_id,
                "session_id": context.session_id,
                "run_id": context.run_id,
                "project_id": context.project_id,
                "project_root_hash": self._root_hash(workspace),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workspace_hash": _workspace_hash(entries),
                "files": entries,
            }
            manifest_bytes = _canonical_json(manifest)
            (temporary / "manifest.json").write_bytes(manifest_bytes)
            (temporary / "manifest.sha256").write_text(
                hashlib.sha256(manifest_bytes).hexdigest(), encoding="ascii"
            )
            final = self.root / snapshot_id
            if final.exists():
                raise RuntimeStorageError("SNAPSHOT_ID_COLLISION")
            temporary.replace(final)
            return snapshot_id
        except (OSError, RuntimeStorageError):
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise RuntimeStorageError("SNAPSHOT_CREATE_FAILED") from exc

    def _load_verified_manifest(
        self, context: ExecutionContext, snapshot_id: str
    ) -> tuple[Path, dict[str, Any]]:
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise RuntimeValidationError("SNAPSHOT_ID_INVALID")
        snapshot = (self.root / snapshot_id).resolve()
        try:
            snapshot.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeValidationError("SNAPSHOT_ID_INVALID") from exc
        if not snapshot.is_dir():
            raise RuntimeValidationError("SNAPSHOT_MISSING")
        manifest_path = snapshot / "manifest.json"
        digest_path = snapshot / "manifest.sha256"
        try:
            manifest_bytes = manifest_path.read_bytes()
            expected_manifest_hash = digest_path.read_text(encoding="ascii").strip()
            if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_hash:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED") from exc
        if not isinstance(manifest, dict):
            raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
        if (
            manifest.get("snapshot_id") != snapshot_id
            or manifest.get("session_id") != context.session_id
            or manifest.get("project_id") != context.project_id
            or manifest.get("project_root_hash")
            != self._root_hash(Path(context.project_root).resolve())
        ):
            raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
        return snapshot, manifest

    def _verified_entries(
        self,
        context: ExecutionContext,
        snapshot: Path,
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_entries = manifest.get("files")
        if not isinstance(raw_entries, list):
            raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
        entries: list[dict[str, Any]] = []
        files_root = (snapshot / "files").resolve()
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            relative = raw.get("path")
            size = raw.get("size")
            file_hash = raw.get("sha256")
            if (
                not _path_is_safe(relative)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(file_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", file_hash)
                or self._is_control_plane(relative)
                or self._is_sensitive(relative)
            ):
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            try:
                self._path_policy.assert_path(
                    context.role, context.project_root, relative, operation="write"
                )
            except RuntimeValidationError as exc:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED") from exc
            source = (files_root / Path(*PurePosixPath(relative).parts)).resolve()
            try:
                source.relative_to(files_root)
            except ValueError as exc:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED") from exc
            if not source.is_file() or source.is_symlink():
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            actual_hash, actual_size = _sha256_file(source)
            if actual_hash != file_hash or actual_size != size:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            entries.append({"path": relative, "size": size, "sha256": file_hash})
        entries.sort(key=lambda value: str(value["path"]))
        if manifest.get("workspace_hash") != _workspace_hash(entries):
            raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
        return entries

    @staticmethod
    def _verify_materialized_workspace(root: Path, entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            target = (root / Path(*PurePosixPath(entry["path"]).parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED") from exc
            if not target.is_file():
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")
            actual_hash, actual_size = _sha256_file(target)
            if actual_hash != entry["sha256"] or actual_size != entry["size"]:
                raise RuntimeValidationError("RESTORE_VERIFICATION_FAILED")

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def restore(self, context: ExecutionContext, snapshot_id: str) -> None:
        """验证完整 Snapshot 后，以受控交换恢复 workspace。"""

        workspace = Path(context.project_root).resolve()
        snapshot, manifest = self._load_verified_manifest(context, snapshot_id)
        entries = self._verified_entries(context, snapshot, manifest)
        self._assert_storage_outside_workspace(workspace)
        parent = workspace.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".snapshot-restore-", dir=str(parent)))
        backup = Path(tempfile.mkdtemp(prefix=".snapshot-backup-", dir=str(parent)))
        preserved = {name.casefold() for name in _CONTROL_PLANE_NAMES}
        try:
            for entry in entries:
                source = snapshot / "files" / Path(*PurePosixPath(entry["path"]).parts)
                target = staging / Path(*PurePosixPath(entry["path"]).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            self._verify_materialized_workspace(staging, entries)

            workspace.mkdir(parents=True, exist_ok=True)
            for child in list(workspace.iterdir()):
                if child.name.casefold() in preserved:
                    continue
                shutil.move(str(child), str(backup / child.name))
            for child in list(staging.iterdir()):
                shutil.move(str(child), str(workspace / child.name))
            self._verify_materialized_workspace(workspace, entries)
        except Exception as exc:
            try:
                workspace.mkdir(parents=True, exist_ok=True)
                for child in list(workspace.iterdir()):
                    if child.name.casefold() not in preserved:
                        self._remove_path(child)
                for child in list(backup.iterdir()):
                    shutil.move(str(child), str(workspace / child.name))
            except Exception as rollback_exc:
                raise RuntimeStorageError("RESTORE_ROLLBACK_FAILED") from rollback_exc
            if isinstance(exc, RuntimeValidationError):
                raise
            raise RuntimeStorageError("RESTORE_COMMIT_FAILED") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
