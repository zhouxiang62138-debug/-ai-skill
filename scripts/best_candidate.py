"""Runtime 可验证的 Best Candidate 选择与恢复建议。"""

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
HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
LEGACY_CANDIDATE_FIELDS = frozenset(
    {
        "schema_version", "candidate_id", "snapshot_id", "evaluation_id",
        "project_revision", "score", "blocking_issues", "critical_issues",
        "regression_status", "feature_completeness", "browser_acceptance", "validated_at",
    }
)
BOUND_CANDIDATE_FIELDS = LEGACY_CANDIDATE_FIELDS | frozenset(
    {
        "evaluation_profile", "evaluation_profile_hash", "rubric_version",
        "evaluator_model", "calibration_suite_version", "required_gate_results",
    }
)
OPTIONAL_BOUND_CANDIDATE_FIELDS = frozenset({"workspace_hash"})


def _error(message: str) -> ProjectStateError:
    return ProjectStateError(f"Best Candidate 无效：{message}")


def _string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} 必须是非空字符串")


def _string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise _error(f"{label} 必须是字符串列表")


def _hash(value: Any, label: str) -> None:
    _string(value, label)
    if not HASH_PATTERN.fullmatch(value):
        raise _error(f"{label} 必须是 SHA-256 十六进制摘要")


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
    if not isinstance(candidate, Mapping):
        raise _error("Candidate 顶层必须是对象")
    version = candidate.get("schema_version")
    expected_fields = LEGACY_CANDIDATE_FIELDS if version == 1 else BOUND_CANDIDATE_FIELDS
    if not set(candidate) <= expected_fields | OPTIONAL_BOUND_CANDIDATE_FIELDS or not expected_fields <= set(candidate):
        raise _error("Candidate 字段不完整或包含未声明字段")
    if version not in {1, 2}:
        raise _error("schema_version 只能为 1（legacy read-only）或 2")
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
    if version == 2:
        _string(candidate["evaluation_profile"], "evaluation_profile")
        _hash(candidate["evaluation_profile_hash"], "evaluation_profile_hash")
        _string(candidate["rubric_version"], "rubric_version")
        _string(candidate["evaluator_model"], "evaluator_model")
        _string(candidate["calibration_suite_version"], "calibration_suite_version")
        gates = candidate["required_gate_results"]
        if not isinstance(gates, Mapping) or not gates:
            raise _error("required_gate_results 必须是非空对象")
        if any(
            not isinstance(key, str) or not key.strip() or value not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}
            for key, value in gates.items()
        ):
            raise _error("required_gate_results 包含无效结果")
        if "workspace_hash" in candidate:
            _hash(candidate["workspace_hash"], "workspace_hash")
    if SECRET_PATTERN.search(str(candidate)):
        raise _error("Candidate 不得包含疑似 Secret")
    if policy is not None:
        allowed_browser = policy.get("allowed_browser_acceptance")
        if not isinstance(allowed_browser, list) or any(not isinstance(value, str) for value in allowed_browser):
            raise _error("allowed_browser_acceptance 必须是字符串列表")


def _profile_requires_browser(
    candidate: Mapping[str, Any], policy: Mapping[str, Any], profile: Mapping[str, Any] | None = None
) -> bool:
    if profile is not None:
        browser = profile.get("browser_validation", profile)
        if isinstance(browser, Mapping):
            return browser.get("required") is True
    profile_id = candidate.get("evaluation_profile")
    required_profiles = policy.get("browser_required_profiles", [])
    return isinstance(required_profiles, list) and profile_id in required_profiles


def _candidate_is_valid(
    candidate: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    profile: Mapping[str, Any] | None = None,
) -> bool:
    required_regression = policy.get("required_regression_status", "PASS")
    max_blocking = policy.get("maximum_blocking_issues", 0)
    max_critical = policy.get("maximum_critical_issues", 0)
    allowed_browser = policy.get("allowed_browser_acceptance", ["PASS", "SKIPPED"])
    minimum_feature = _decimal(policy.get("minimum_feature_completeness_score", "8.0"), "minimum_feature_completeness_score")
    try:
        feature_score = Decimal(str(candidate["feature_completeness"]))
    except InvalidOperation:
        feature_score = Decimal("-1")
    if candidate.get("schema_version") == 2:
        gate_results = candidate.get("required_gate_results", {})
        required_gates = _required_gate_ids(candidate, policy, profile=profile)
        if not required_gates <= set(gate_results):
            return False
        if any(gate_results.get(gate) != "PASS" for gate in required_gates):
            return False
        if _profile_requires_browser(candidate, policy, profile=profile) and candidate["browser_acceptance"] != "PASS":
            return False
    return (
        len(candidate["blocking_issues"]) <= max_blocking
        and len(candidate["critical_issues"]) <= max_critical
        and candidate["regression_status"] == required_regression
        and candidate["browser_acceptance"] in allowed_browser
        and feature_score >= minimum_feature
    )


def _required_gate_ids(
    candidate: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    profile: Mapping[str, Any] | None = None,
) -> set[str]:
    """推导当前 Profile 的完整必需 Gate 集合。"""

    if profile is not None:
        evaluation = profile.get("evaluation", profile)
        gates = evaluation.get("gates", {}) if isinstance(evaluation, Mapping) else {}
        if isinstance(gates, Mapping):
            return {str(key) for key, value in gates.items() if isinstance(value, Mapping) and value.get("required") is True}
    configured = policy.get("required_gate_sets", {})
    if isinstance(configured, Mapping):
        values = configured.get(candidate.get("evaluation_profile"), [])
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            return set(values)
    return set()


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
    if candidate.get("schema_version") == 1:
        raise _error("legacy Candidate v1 只允许历史 read-only 读取，不能新增")
    # 新版 Candidate 在写入时就拒绝绕过必需 Gate；旧版历史只读保留原语义。
    if candidate.get("schema_version") == 2 and not _candidate_is_valid(candidate, policy):
        raise _error("Profile-bound Candidate 未通过必需 Gate，不能写入")
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
    profile: Mapping[str, Any] | None = None,
    require_profile_bound: bool = False,
) -> dict[str, Any] | None:
    """按验证有效性、分数和稳定编号选择最佳 Candidate。"""

    root = Path(project_root).resolve()
    policy = _load_policy(policy_path)
    candidates = _load_candidates(root, policy_path)
    valid = [
        candidate
        for candidate in candidates
        if _candidate_is_valid(candidate, policy, profile=profile)
        and (not require_profile_bound or candidate.get("schema_version") == 2)
    ]
    if not valid:
        return None
    cohorts = {
        (
            candidate.get("evaluation_profile"),
            candidate.get("evaluation_profile_hash"),
            candidate.get("rubric_version"),
            candidate.get("evaluator_model"),
            candidate.get("calibration_suite_version"),
        )
        for candidate in valid
        if candidate.get("schema_version") == 2
    }
    if cohorts:
        valid = [candidate for candidate in valid if candidate.get("schema_version") == 2]
    if len(cohorts) > 1:
        raise _error("不同 Rubric/Profile/Calibration/Evaluator Model 的 Candidate 不可直接比较")
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
    evaluation_profile: str | None = None,
    evaluation_profile_hash: str | None = None,
    rubric_version: str | None = None,
    calibration_suite_version: str | None = None,
    evaluator_model: str | None = None,
    profile: Mapping[str, Any] | None = None,
    legacy_read_only: bool = False,
) -> dict[str, Any]:
    """读取并校验指定 Candidate，供 Runtime Snapshot Service 使用。"""

    if not CANDIDATE_ID_PATTERN.fullmatch(candidate_id):
        raise _error("candidate_id 格式无效")
    for candidate in _load_candidates(project_root, policy_path):
        if candidate["candidate_id"] == candidate_id:
            policy = _load_policy(policy_path)
            if candidate.get("schema_version") == 1 and not legacy_read_only:
                raise _error("legacy Candidate 默认不可恢复，必须显式 legacy_read_only")
            if candidate.get("schema_version") == 1 and any(
                value is not None
                for value in (
                    evaluation_profile,
                    evaluation_profile_hash,
                    rubric_version,
                    calibration_suite_version,
                    profile,
                )
            ):
                raise _error("legacy Candidate 只能显式按 read-only 方式读取，不能绑定新 Profile")
            if candidate.get("schema_version") == 2:
                if not all(
                    value is not None
                    for value in (
                        evaluation_profile,
                        evaluation_profile_hash,
                        rubric_version,
                        calibration_suite_version,
                    )
                ):
                    raise _error("Candidate v2 必须完整绑定当前 Profile、Hash、Rubric 和 Calibration")
                expected = {
                    "evaluation_profile": evaluation_profile,
                    "evaluation_profile_hash": evaluation_profile_hash,
                    "rubric_version": rubric_version,
                    "calibration_suite_version": calibration_suite_version,
                }
                for field, value in expected.items():
                    if value is not None and candidate.get(field) != value:
                        raise _error(f"Candidate 与绑定的 {field} 不一致")
                if evaluator_model is not None and candidate.get("evaluator_model") != evaluator_model:
                    raise _error("Candidate 与绑定的 evaluator_model 不一致")
            if not _candidate_is_valid(candidate, policy, profile=profile):
                raise _error("只能恢复满足验证条件的 Candidate")
            return candidate
    raise _error("Candidate 不存在")


def append_restore_recommendation(
    project_root: str | Path,
    *,
    current_candidate_id: str | None,
    created_at: str,
    policy_path: str | Path | None = None,
    profile: Mapping[str, Any] | None = None,
) -> Path | None:
    """追加恢复建议；只写建议，绝不直接调用 Snapshot restore。"""

    root = Path(project_root).resolve()
    best = best_validated_candidate(root, policy_path=policy_path, profile=profile)
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
