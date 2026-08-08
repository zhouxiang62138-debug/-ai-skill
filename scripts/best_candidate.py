"""Stage 7 最佳已验证 Candidate 选择与恢复建议。"""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import ProjectStateError, parse_project_yaml, serialize_project_state


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "candidate_policy.yaml"
CANDIDATE_DIRECTORY = Path("evaluation") / "candidates"
CANDIDATE_PATTERN = re.compile(r"^candidate-(\d{3})\.yaml$")
CANDIDATE_ID_PATTERN = re.compile(r"^candidate-(\d{3})$")
RECOMMENDATION_PATTERN = re.compile(r"^recommendation-(\d{3})\.yaml$")
SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]")


def _error(message: str) -> ProjectStateError:
    return ProjectStateError(f"Best Candidate 无效：{message}")


def _string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} 必须是非空字符串")


def _string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise _error(f"{label} 必须是字符串列表")


def _decimal(value: Any, label: str) -> Decimal:
    _string(value, label)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise _error(f"{label} 必须是非负分数") from exc
    if parsed < 0 or not parsed.is_finite():
        raise _error(f"{label} 必须是非负分数")
    return parsed


def _load_policy(policy_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(policy_path or DEFAULT_POLICY_PATH)
    try:
        value = parse_project_yaml(path.read_text(encoding="utf-8"))
    except (OSError, ProjectStateError) as exc:
        raise _error("无法读取 Candidate 策略") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise _error("Candidate 策略 schema_version 必须为 1")
    return value


def _candidate_files(project_root: Path) -> list[Path]:
    directory = project_root / CANDIDATE_DIRECTORY
    if not directory.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in directory.iterdir():
        match = CANDIDATE_PATTERN.fullmatch(path.name)
        if match and path.is_file():
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found)]


def validate_candidate(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> None:
    """校验 Candidate 是否满足可比较的结构和安全边界。"""

    fields = {
        "schema_version", "candidate_id", "snapshot_id", "evaluation_id",
        "project_revision", "score", "blocking_issues", "critical_issues",
        "regression_status", "feature_completeness", "browser_acceptance", "validated_at",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != fields:
        raise _error("Candidate 字段不完整或包含未声明字段")
    if candidate["schema_version"] != 1:
        raise _error("schema_version 必须为 1")
    _string(candidate["candidate_id"], "candidate_id")
    _string(candidate["snapshot_id"], "snapshot_id")
    if not re.fullmatch(r"snapshot-[A-Za-z0-9_-]+", candidate["snapshot_id"]):
        raise _error("snapshot_id 格式无效")
    _string(candidate["evaluation_id"], "evaluation_id")
    if not re.fullmatch(r"evaluation-\d{3}", candidate["evaluation_id"]):
        raise _error("evaluation_id 格式无效")
    revision = candidate["project_revision"]
    if not isinstance(revision, int) or revision < 0:
        raise _error("project_revision 必须是非负整数")
    _decimal(candidate["score"], "score")
    _string_list(candidate["blocking_issues"], "blocking_issues")
    _string_list(candidate["critical_issues"], "critical_issues")
    if candidate["regression_status"] not in {"PASS", "FAIL", "BLOCKED"}:
        raise _error("regression_status 无效")
    _string(candidate["feature_completeness"], "feature_completeness")
    if candidate["browser_acceptance"] not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
        raise _error("browser_acceptance 无效")
    _string(candidate["validated_at"], "validated_at")
    if SECRET_PATTERN.search(str(candidate)):
        raise _error("Candidate 不得包含疑似 Secret")
    if policy is not None:
        allowed_browser = policy.get("allowed_browser_acceptance")
        if not isinstance(allowed_browser, list) or any(not isinstance(value, str) for value in allowed_browser):
            raise _error("allowed_browser_acceptance 必须是字符串列表")


def _candidate_is_valid(candidate: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    required_regression = policy.get("required_regression_status", "PASS")
    max_blocking = policy.get("maximum_blocking_issues", 0)
    max_critical = policy.get("maximum_critical_issues", 0)
    allowed_browser = policy.get("allowed_browser_acceptance", ["PASS", "SKIPPED"])
    minimum_feature = _decimal(policy.get("minimum_feature_completeness_score", "8.0"), "minimum_feature_completeness_score")
    try:
        feature_score = Decimal(str(candidate["feature_completeness"]))
    except InvalidOperation:
        feature_score = Decimal("-1")
    return (
        len(candidate["blocking_issues"]) <= max_blocking
        and len(candidate["critical_issues"]) <= max_critical
        and candidate["regression_status"] == required_regression
        and candidate["browser_acceptance"] in allowed_browser
        and feature_score >= minimum_feature
    )


def _load_candidates(project_root: str | Path, policy_path: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(project_root).resolve()
    policy = _load_policy(policy_path)
    records: list[dict[str, Any]] = []
    for index, path in enumerate(_candidate_files(root), 1):
        try:
            record = parse_project_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ProjectStateError) as exc:
            raise _error(f"无法读取 {path.name}") from exc
        if not isinstance(record, dict):
            raise _error(f"{path.name} 顶层必须是对象")
        validate_candidate(record, policy=policy)
        expected = f"candidate-{index:03d}"
        if record["candidate_id"] != expected or path.name != f"{expected}.yaml":
            raise _error("Candidate 编号或文件名不连续")
        records.append(record)
    return records


def append_candidate(
    project_root: str | Path,
    candidate: Mapping[str, Any],
    *,
    policy_path: str | Path | None = None,
) -> Path:
    """追加一条 Evaluator Candidate，拒绝覆盖历史记录。"""

    root = Path(project_root).resolve()
    policy = _load_policy(policy_path)
    paths = _candidate_files(root)
    if paths:
        _load_candidates(root, policy_path)
    expected = f"candidate-{len(paths) + 1:03d}"
    if candidate.get("candidate_id") != expected:
        raise _error(f"下一条 Candidate 必须使用 {expected}")
    validate_candidate(candidate, policy=policy)
    directory = root / CANDIDATE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{expected}.yaml"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise _error(f"拒绝覆盖已有 Candidate：{target.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialize_project_state(dict(candidate)).encode("utf-8"))
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return target


def best_validated_candidate(
    project_root: str | Path,
    *,
    policy_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """按验证有效性、分数和稳定编号选择最佳 Candidate。"""

    root = Path(project_root).resolve()
    policy = _load_policy(policy_path)
    candidates = _load_candidates(root, policy_path)
    valid = [candidate for candidate in candidates if _candidate_is_valid(candidate, policy)]
    if not valid:
        return None
    return max(
        valid,
        key=lambda candidate: (
            _decimal(candidate["score"], "score"),
            -int(candidate["candidate_id"].split("-")[-1]),
        ),
    )


def load_candidate(
    project_root: str | Path,
    candidate_id: str,
    *,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """读取并校验指定 Candidate，供 Runtime Snapshot Service 使用。"""

    if not CANDIDATE_ID_PATTERN.fullmatch(candidate_id):
        raise _error("candidate_id 格式无效")
    for candidate in _load_candidates(project_root, policy_path):
        if candidate["candidate_id"] == candidate_id:
            policy = _load_policy(policy_path)
            if not _candidate_is_valid(candidate, policy):
                raise _error("只能恢复满足验证条件的 Candidate")
            return candidate
    raise _error("Candidate 不存在")


def append_restore_recommendation(
    project_root: str | Path,
    *,
    current_candidate_id: str | None,
    created_at: str,
    policy_path: str | Path | None = None,
) -> Path | None:
    """追加恢复建议；只写建议，绝不直接调用 Snapshot restore。"""

    root = Path(project_root).resolve()
    best = best_validated_candidate(root, policy_path=policy_path)
    if best is None or best["candidate_id"] == current_candidate_id:
        return None
    if current_candidate_id is not None and not CANDIDATE_ID_PATTERN.fullmatch(current_candidate_id):
        raise _error("current_candidate_id 格式无效")
    directory = root / CANDIDATE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        path for path in directory.iterdir()
        if RECOMMENDATION_PATTERN.fullmatch(path.name) and path.is_file()
    )
    number = len(existing) + 1
    recommendation = {
        "schema_version": 1,
        "recommendation_id": f"recommendation-{number:03d}",
        "action": "recommend_restore_candidate",
        "recommended_candidate_id": best["candidate_id"],
        "current_candidate_id": current_candidate_id,
        "reason": "当前版本不是分数最高的有效 Candidate",
        "no_restore_performed": True,
        "created_at": created_at,
    }
    target = directory / f"recommendation-{number:03d}.yaml"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise _error(f"拒绝覆盖已有恢复建议：{target.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialize_project_state(recommendation).encode("utf-8"))
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return target


__all__ = [
    "append_candidate",
    "append_restore_recommendation",
    "best_validated_candidate",
    "load_candidate",
    "validate_candidate",
]
