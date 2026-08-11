"""基于 role_policies.yaml 的 Execution 文件路径策略。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NoReturn
from scripts.project_state import parse_project_yaml

from runtime.errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE_PATH_PART = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|secret|password|private[_-]?key|credential|token)"
)


class PathAccessDenied(RuntimeValidationError):
    """Path Policy 拒绝后的结构化结果，审计由可信 Host 层负责。"""

    def __init__(
        self,
        message: str,
        *,
        role: str,
        operation: str,
        resource_reference: str,
        resource_class: str,
        reason_code: str,
        policy_source: str = "config/role_policies.yaml",
    ) -> None:
        super().__init__(message)
        self.role = role
        self.operation = operation
        self.resource_reference = resource_reference
        self.resource_class = resource_class
        self.reason_code = reason_code
        self.policy_source = policy_source


def _looks_like_control_plane(value: object) -> bool:
    """只用路径类别判断 Control Plane，不把主机绝对路径写入事件。"""

    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/").casefold()
    segments = {
        item for item in normalized.split("/") if item and item not in {".", ".."}
    }
    return (
        "/.ai-development-team/runtime/" in f"/{normalized}/"
        or "sessions.sqlite3" in segments
        or (
            any(item.startswith("runtime-") for item in segments)
            and "sessions.sqlite3" in segments
        )
    )


def _safe_resource_reference(project_root: str | Path, value: object) -> str:
    """返回可审计但不泄露主机目录的路径引用。"""

    if not isinstance(value, str) or not value:
        digest = hashlib.sha256(repr(value).encode("utf-8", "replace")).hexdigest()[:16]
        return f"input:{digest}"
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        normalized.startswith("/")
        or pure.is_absolute()
        or bool(PureWindowsPath(normalized).drive)
        or ".." in pure.parts
    ):
        digest = hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:16]
        return f"outside-project:{digest}"
    safe_parts = []
    for part in pure.parts:
        if _SENSITIVE_PATH_PART.search(part):
            digest = hashlib.sha256(part.encode("utf-8", "replace")).hexdigest()[:16]
            safe_parts.append(f"path-hash:{digest}")
        else:
            safe_parts.append(part)
    relative = "/".join(safe_parts) or "."
    return f"project:{relative}"


def _resource_class(value: object, operation: str) -> str:
    if _looks_like_control_plane(value):
        return "control_plane"
    if operation == "execute":
        return "working_directory"
    return "project_path"


def _reparse_escape_kind(project_root: str | Path, value: object) -> str | None:
    """识别导致解析越界的 symlink 或 Windows junction。"""

    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        normalized.startswith("/")
        or pure.is_absolute()
        or bool(PureWindowsPath(normalized).drive)
        or ".." in pure.parts
    ):
        return None
    current = Path(project_root).resolve()
    for part in pure.parts:
        if part in {"", "."}:
            continue
        current = current / part
        try:
            if current.is_symlink():
                return "symlink"
            is_junction = getattr(current, "is_junction", None)
            if callable(is_junction) and is_junction():
                return "junction"
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
            if os.name == "nt" and attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                return "junction"
        except OSError:
            break
    return None


def _relative_reason(project_root: str | Path, value: object) -> str:
    if _looks_like_control_plane(value):
        return "CONTROL_PLANE_ACCESS_DENIED"
    kind = _reparse_escape_kind(project_root, value)
    if kind == "junction":
        return "JUNCTION_ESCAPE_DENIED"
    if kind == "symlink":
        return "SYMLINK_ESCAPE_DENIED"
    return "PATH_OUTSIDE_PROJECT"


def _matches(path: str, configured: object) -> bool:
    if not isinstance(configured, str):
        return False
    prefix = configured.replace("\\", "/").strip("/")
    if not prefix:
        return path == "."
    return path == prefix or path.startswith(prefix + "/")


class ExecutionPathPolicy:
    """校验项目内路径和角色读写边界。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path) if config_path else _ROOT / "config" / "role_policies.yaml"
        document = parse_project_yaml(path.read_text(encoding="utf-8"))
        roles = document.get("roles")
        if not isinstance(roles, dict):
            raise RuntimeValidationError("ROLE_POLICY_INVALID")
        self._roles: dict[str, dict[str, tuple[str, ...]]] = {}
        for role, policy in roles.items():
            if not isinstance(role, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("ROLE_POLICY_INVALID")
            parsed: dict[str, tuple[str, ...]] = {}
            for key in (
                "reads",
                "writes",
                "prohibited",
                "write_prohibited",
                "read_exceptions",
            ):
                values = policy.get(key, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise RuntimeValidationError("ROLE_POLICY_INVALID")
                parsed[key] = tuple(values)
            self._roles[role] = parsed
        modules = document.get("modules", {})
        if not isinstance(modules, dict):
            raise RuntimeValidationError("MODULE_POLICY_INVALID")
        self._modules: dict[str, dict[str, tuple[str, ...]]] = {}
        for module, policy in modules.items():
            if not isinstance(module, str) or not isinstance(policy, dict):
                raise RuntimeValidationError("MODULE_POLICY_INVALID")
            parsed = {}
            for key in (
                "reads",
                "writes",
                "prohibited",
                "write_prohibited",
                "read_exceptions",
            ):
                values = policy.get(key, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise RuntimeValidationError("MODULE_POLICY_INVALID")
                parsed[key] = tuple(values)
            self._modules[module] = parsed

    @staticmethod
    def _raise_denied(
        message: str,
        *,
        role: str,
        operation: str,
        project_root: str | Path,
        path: object,
        reason_code: str,
    ) -> NoReturn:
        raise PathAccessDenied(
            message,
            role=role,
            operation=operation,
            resource_reference=_safe_resource_reference(project_root, path),
            resource_class=_resource_class(path, operation),
            reason_code=reason_code,
        )

    @staticmethod
    def _decorate_relative_denial(
        exc: RuntimeValidationError,
        *,
        role: str,
        operation: str,
        project_root: str | Path,
        path: object,
    ) -> PathAccessDenied:
        return PathAccessDenied(
            str(exc),
            role=role,
            operation=operation,
            resource_reference=_safe_resource_reference(project_root, path),
            resource_class=_resource_class(path, operation),
            reason_code=_relative_reason(project_root, path),
        )

    @staticmethod
    def _relative(project_root: str | Path, value: str) -> tuple[Path, str]:
        if not isinstance(value, str) or not value:
            raise RuntimeValidationError("执行路径不能为空")
        normalized = value.replace("\\", "/")
        pure = PurePosixPath(normalized)
        windows_path = PureWindowsPath(normalized)
        if (
            normalized.startswith("/")
            or pure.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in pure.parts
        ):
            raise RuntimeValidationError("路径必须是项目内相对路径")
        relative = "." if normalized in {"", "."} else "/".join(pure.parts)
        root = Path(project_root).resolve()
        candidate = (root / Path(*pure.parts)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise RuntimeValidationError("符号链接或路径解析逃出项目根目录") from exc
        return candidate, relative

    def assert_path(
        self, role: str, project_root: str | Path, path: str, *, operation: str
    ) -> Path:
        """校验 read/write 路径，并返回解析后的宿主路径。"""

        if operation not in {"read", "write"}:
            raise RuntimeValidationError("EXECUTION_PATH_OPERATION_INVALID")
        policy = self._roles.get(role)
        if policy is None:
            self._raise_denied(
                "ROLE_POLICY_UNKNOWN_ACTOR",
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="ROLE_PATH_DENIED",
            )
        try:
            candidate, relative = self._relative(project_root, path)
        except RuntimeValidationError as exc:
            raise self._decorate_relative_denial(
                exc,
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
            ) from exc
        root = Path(project_root).resolve()
        resolved_relative = candidate.relative_to(root).as_posix()
        if operation == "write" and (
            relative.casefold() == "project.yaml"
            or resolved_relative.casefold() == "project.yaml"
        ):
            self._raise_denied(
                "PROJECT_STATE_WRITE_REQUIRES_CAS",
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="PROJECT_YAML_DIRECT_WRITE_DENIED",
            )
        read_exception = operation == "read" and any(
            _matches(relative, item) for item in policy["read_exceptions"]
        )
        if not read_exception and any(
            _matches(relative, item) for item in policy["prohibited"]
        ):
            self._raise_denied(
                "EXECUTION_PATH_PROHIBITED",
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="ROLE_PATH_DENIED",
            )
        if operation == "write" and any(
            _matches(relative, item) for item in policy["write_prohibited"]
        ):
            self._raise_denied(
                "EXECUTION_PATH_WRITE_PROHIBITED",
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="ROLE_PATH_DENIED",
            )
        allowed = policy["reads"] if operation == "read" else policy["writes"]
        if not any(_matches(relative, item) for item in allowed):
            self._raise_denied(
                "EXECUTION_PATH_NOT_ALLOWED",
                role=role,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="ROLE_PATH_DENIED",
            )
        return candidate

    def assert_module_path(
        self, module: str, project_root: str | Path, path: str, *, operation: str
    ) -> Path:
        """复用同一套 F11 路径解析，按 modules 配置校验 Module 读写。"""

        if operation not in {"read", "write"}:
            raise RuntimeValidationError("EXECUTION_PATH_OPERATION_INVALID")
        policy = self._modules.get(module)
        if policy is None:
            self._raise_denied(
                "MODULE_POLICY_UNKNOWN_ACTOR",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="MODULE_PATH_DENIED",
            )
        try:
            candidate, relative = self._relative(project_root, path)
        except RuntimeValidationError as exc:
            raise self._decorate_relative_denial(
                exc,
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
            ) from exc
        root = Path(project_root).resolve()
        resolved_relative = candidate.relative_to(root).as_posix()
        if operation == "write" and (
            relative.casefold() == "project.yaml"
            or resolved_relative.casefold() == "project.yaml"
        ):
            self._raise_denied(
                "PROJECT_STATE_WRITE_REQUIRES_CAS",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="PROJECT_YAML_DIRECT_WRITE_DENIED",
            )
        reparsed = _reparse_escape_kind(project_root, path)
        if reparsed is not None:
            self._raise_denied(
                "MODULE_REPARSE_POINT_DENIED",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code=f"{reparsed.upper()}_DENIED",
            )
        read_exception = operation == "read" and any(
            _matches(relative, item) for item in policy["read_exceptions"]
        )
        if not read_exception and any(
            _matches(relative, item) for item in policy["prohibited"]
        ):
            self._raise_denied(
                "MODULE_PATH_PROHIBITED",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="MODULE_PATH_DENIED",
            )
        if operation == "write" and any(
            _matches(relative, item) for item in policy["write_prohibited"]
        ):
            self._raise_denied(
                "MODULE_PATH_WRITE_PROHIBITED",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="MODULE_PATH_DENIED",
            )
        allowed = policy["reads"] if operation == "read" else policy["writes"]
        if not any(_matches(relative, item) for item in allowed):
            self._raise_denied(
                "MODULE_PATH_NOT_ALLOWED",
                role=module,
                operation=operation,
                project_root=project_root,
                path=path,
                reason_code="MODULE_PATH_DENIED",
            )
        return candidate

    def assert_cwd(self, project_root: str | Path, cwd: str) -> Path:
        """只允许执行工作目录位于项目根内。"""

        try:
            candidate, _ = self._relative(project_root, cwd)
        except RuntimeValidationError as exc:
            raise self._decorate_relative_denial(
                exc,
                role="runtime",
                operation="execute",
                project_root=project_root,
                path=cwd,
            ) from exc
        if not candidate.is_dir():
            self._raise_denied(
                "cwd 必须是项目内已存在目录",
                role="runtime",
                operation="execute",
                project_root=project_root,
                path=cwd,
                reason_code="CWD_NOT_DIRECTORY",
            )
        return candidate
