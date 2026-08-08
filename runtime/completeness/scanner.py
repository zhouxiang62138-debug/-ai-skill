"""项目代码的确定性 Anti-Stub 静态扫描器。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .models import FeatureFinding


_TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".jsx",
    ".js",
    ".json",
    ".kt",
    ".py",
    ".rb",
    ".rs",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}
_RULES: tuple[tuple[str, str, str, float], ...] = (
    ("todo_marker", r"\b(?:TODO|FIXME)\b", "major", 1.5),
    (
        "placeholder",
        r"\b(?:placeholder|coming soon|not implemented|implement here)\b",
        "critical",
        3.0,
    ),
    (
        "mock_or_fake_data",
        r"\b(?:mock data|fake data|mock-only|fixture data|sample data)\b",
        "major",
        2.0,
    ),
    (
        "empty_callback",
        r"(?:=>\s*\{\s*\}|on(?:Click|Change|Submit)\s*=\s*['\"]\s*['\"])",
        "critical",
        3.0,
    ),
    (
        "no_op_handler",
        r"^\s*(?:pass\s*#?.*|return\s+None\s*#?.*)$",
        "major",
        1.5,
    ),
)


def _iter_code_files(root: Path, roots: Iterable[str]) -> Iterable[Path]:
    for relative_root in roots:
        base = (root / relative_root).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            if any(part in {".git", ".runtime", "node_modules", "__pycache__"} for part in path.parts):
                continue
            yield path


def scan_project(
    project_root: str | Path,
    *,
    roots: Iterable[str] = ("code",),
    max_file_bytes: int = 1_000_000,
) -> list[FeatureFinding]:
    """只读扫描项目代码并返回稳定排序的 Finding。"""

    root = Path(project_root).resolve()
    findings: list[FeatureFinding] = []
    for path in _iter_code_files(root, roots):
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(lines, 1):
            for category, pattern, severity, penalty in _RULES:
                if re.search(pattern, line, flags=re.IGNORECASE):
                    findings.append(
                        FeatureFinding(
                            finding_id=f"FC-FIND-{len(findings) + 1:03d}",
                            category=category,
                            severity=severity,
                            path=relative,
                            line=line_number,
                            evidence=line.strip()[:500],
                            penalty=penalty,
                        )
                    )
    return findings
