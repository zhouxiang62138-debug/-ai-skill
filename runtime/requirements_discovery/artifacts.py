"""需求发现追加式工件存储。

工件写入仍通过 First-Ask 的 Path Policy；Runtime 状态只保存相对路径指针。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.errors import RuntimeValidationError
from runtime.execution.path_policy import ExecutionPathPolicy


class DiscoveryArtifactStore:
    """只允许追加、不允许覆盖已有不同内容的需求发现工件。"""

    def __init__(
        self,
        root: str | Path,
        *,
        actor: str = "first_ask_intake",
        path_policy: ExecutionPathPolicy | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.actor = actor
        self.path_policy = path_policy or ExecutionPathPolicy()

    def _path(self, relative: str, *, write: bool) -> Path:
        return self.path_policy.assert_module_path(self.actor, self.root, relative, operation="write" if write else "read")

    def read(self, relative: str) -> dict[str, Any]:
        path = self._path(relative, write=False)
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeValidationError("DISCOVERY_ARTIFACT_READ_FAILED") from exc
        if not isinstance(value, dict):
            raise RuntimeValidationError("DISCOVERY_ARTIFACT_INVALID")
        return value

    def write(self, relative: str, value: Mapping[str, Any]) -> str:
        target = self._path(relative, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(dict(value), allow_unicode=True, sort_keys=False)
        if target.exists():
            if target.read_text(encoding="utf-8") != content:
                raise RuntimeValidationError("DISCOVERY_ARTIFACT_APPEND_ONLY_CONFLICT")
            return relative
        handle, temporary = tempfile.mkstemp(prefix=".discovery-", dir=str(target.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise RuntimeValidationError("DISCOVERY_ARTIFACT_APPEND_ONLY_CONFLICT")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return relative

    def next_number(self, directory: str, pattern: str, current: int = 0) -> int:
        folder = self._path(directory, write=True)
        highest = max(0, int(current or 0))
        if folder.exists():
            for path in folder.glob("*"):
                match = re.fullmatch(pattern, path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1
