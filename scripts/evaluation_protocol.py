"""F9.1 结构化验收问题、Generator 回应与事务提交协议。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from .project_state import (
        ProjectStateError,
        load_project_state,
        parse_project_yaml,
        serialize_project_state,
        write_project_state_atomic,
    )
except ImportError:  # 兼容 tests 直接把 scripts 加入 sys.path
    from project_state import (
        ProjectStateError,
        load_project_state,
        parse_project_yaml,
        serialize_project_state,
        write_project_state_atomic,
    )


ISSUE_CATEGORIES = (
    "implementation_defect",
    "test_defect",
    "missing_test",
    "incomplete_implementation",
    "regression",
    "plan_gap",
    "scope_mismatch",
    "requirement_ambiguity",
    "environment_blocker",
    "evidence_missing",
    "unauthorized_change",
    "reference_missing",
    "reference_incorrect",
    "reference_exclusion_violation",
    "reference_scope_creep",
    "reference_stale_binding",
    "reference_evidence_missing",
    "reference_capability_blocked",
)
SEVERITY_LEVELS = ("blocker", "critical", "major", "minor", "observation")
TRACEABILITY_STATUSES = (
    "MAPPED",
    "PARTIALLY_MAPPED",
    "UNMAPPED",
    "NOT_APPLICABLE",
)
ISSUE_STATUSES = (
    "OPEN",
    "ACKNOWLEDGED",
    "RESOLVED",
    "REOPENED",
    "DEFERRED",
    "INVALID",
)
RESPONSE_STATUSES = (
    "FIXED",
    "PARTIALLY_FIXED",
    "NOT_FIXED",
    "CANNOT_REPRODUCE",
    "NEEDS_CLARIFICATION",
    "OUT_OF_SCOPE",
)
RESULTS = ("PASS", "FAIL", "BLOCKED")

EVALUATION_PATTERN = re.compile(r"^evaluation-(\d{3})$")
ISSUE_PATTERN = re.compile(r"^EVAL-(\d{3})-(\d{3})$")
HANDOFF_PATTERN = re.compile(r"^handoff-\d{3}$")
RESPONSE_REFERENCE_PATTERN = re.compile(
    r"^memory/handoffs/responses/evaluation-\d{3}-response\.yaml$"
)
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

ROUTE_TABLE = {
    "implementation_defect": ("GENERATOR", "IMPLEMENTING"),
    "test_defect": ("GENERATOR", "IMPLEMENTING"),
    "missing_test": ("GENERATOR", "IMPLEMENTING"),
    "incomplete_implementation": ("GENERATOR", "IMPLEMENTING"),
    "regression": ("GENERATOR", "IMPLEMENTING"),
    "plan_gap": ("PLANNER", "PLANNING"),
    "scope_mismatch": ("PLANNER", "PLANNING"),
    "requirement_ambiguity": ("USER", "WAITING_FOR_USER"),
    "environment_blocker": ("SYSTEM_OR_USER", "BLOCKED"),
    "unauthorized_change": ("GENERATOR", "IMPLEMENTING"),
    "reference_missing": ("GENERATOR", "IMPLEMENTING"),
    "reference_incorrect": ("GENERATOR", "IMPLEMENTING"),
    "reference_exclusion_violation": ("GENERATOR", "IMPLEMENTING"),
    "reference_scope_creep": ("GENERATOR", "IMPLEMENTING"),
    "reference_stale_binding": ("GENERATOR", "IMPLEMENTING"),
    "reference_capability_blocked": ("SYSTEM_OR_USER", "BLOCKED"),
}
EVIDENCE_MISSING_ROUTES = {
    "generator_omission": ("GENERATOR", "IMPLEMENTING"),
    "evaluator_environment": ("SYSTEM_OR_USER", "BLOCKED"),
    "profile_gap": ("PLANNER", "PLANNING"),
}
ROUTE_PRIORITY = {
    "SYSTEM_OR_USER": 50,
    "USER": 40,
    "PLANNER": 30,
    "GENERATOR": 20,
    "ACCEPTED": 10,
}
FUNCTIONAL_CATEGORIES = {
    "implementation_defect",
    "test_defect",
    "missing_test",
    "incomplete_implementation",
    "regression",
    "plan_gap",
    "scope_mismatch",
    "requirement_ambiguity",
}


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_relative_path(
    value: Any,
    *,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> str | None:
    """返回路径错误；仅接受项目内 POSIX 风格相对路径。"""
    if not _is_nonempty_string(value):
        return "必须是非空相对路径"
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return "不得是绝对路径"
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "不得包含空段、当前目录或父目录穿越"
    if allowed_prefixes and not any(
        normalized == prefix.rstrip("/")
        or normalized.startswith(prefix.rstrip("/") + "/")
        for prefix in allowed_prefixes
    ):
        return "不在允许的项目相对路径范围内"
    return None


def evaluation_number(evaluation_id: str) -> str:
    match = EVALUATION_PATTERN.fullmatch(evaluation_id)
    if not match:
        raise ProjectStateError("evaluation_id 格式必须为 evaluation-<三位编号>")
    return match.group(1)


def issue_id(evaluation_id: str, sequence: int) -> str:
    """生成稳定 Issue ID，不允许零或超过三位的序号。"""
    if not isinstance(sequence, int) or not 1 <= sequence <= 999:
        raise ProjectStateError("Issue 序号必须位于 1..999")
    return f"EVAL-{evaluation_number(evaluation_id)}-{sequence:03d}"


def _issue_route(issue: dict[str, Any]) -> tuple[str, str]:
    category = issue.get("category")
    if category == "evidence_missing":
        source = issue.get("evidence_missing_reason")
        if source not in EVIDENCE_MISSING_ROUTES:
            raise ProjectStateError("evidence_missing 必须声明确定性的缺失原因")
        return EVIDENCE_MISSING_ROUTES[source]
    if category == "reference_evidence_missing":
        source = issue.get("reference_evidence_missing_reason", "generator_omission")
        if source not in EVIDENCE_MISSING_ROUTES:
            raise ProjectStateError("reference_evidence_missing 必须声明确定性缺失原因")
        return EVIDENCE_MISSING_ROUTES[source]
    try:
        return ROUTE_TABLE[category]
    except KeyError as exc:
        raise ProjectStateError(f"未知 Issue 分类：{category!r}") from exc


def resolve_issue_route(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """按 BLOCKED > USER > PLANNER > GENERATOR > ACCEPTED 选择唯一合法路由。"""
    if not issues:
        return {"target": "ACCEPTED", "next_status": "ACCEPTED"}
    candidates = [
        _issue_route(item)
        for item in issues
        if item.get("status") in {"OPEN", "REOPENED"}
        and item.get("severity") != "observation"
    ]
    if not candidates:
        return {"target": "ACCEPTED", "next_status": "ACCEPTED"}
    target, status = max(candidates, key=lambda item: ROUTE_PRIORITY[item[0]])
    return {"target": target, "next_status": status}


def _validate_issue(
    issue: dict[str, Any],
    evaluation_id: str,
    index: int,
) -> list[str]:
    errors: list[str] = []
    prefix = f"issues[{index}]"
    current_id = issue.get("issue_id")
    if not isinstance(current_id, str) or not ISSUE_PATTERN.fullmatch(current_id):
        errors.append(f"{prefix}.issue_id 格式无效")
    elif issue.get("first_seen_evaluation"):
        first_seen = issue["first_seen_evaluation"]
        if (
            not isinstance(first_seen, str)
            or not EVALUATION_PATTERN.fullmatch(first_seen)
            or not current_id.startswith(f"EVAL-{evaluation_number(first_seen)}-")
        ):
            errors.append(f"{prefix}.issue_id 与 first_seen_evaluation 不一致")
    elif not current_id.startswith(f"EVAL-{evaluation_number(evaluation_id)}-"):
        errors.append(f"{prefix}.issue_id 与 evaluation_id 不一致")
    if issue.get("category") not in ISSUE_CATEGORIES:
        errors.append(f"{prefix}.category 枚举无效")
    if issue.get("severity") not in SEVERITY_LEVELS:
        errors.append(f"{prefix}.severity 枚举无效")
    if issue.get("status") not in ISSUE_STATUSES:
        errors.append(f"{prefix}.status 枚举无效")
    for field in ("title", "expected_result", "actual_result"):
        if not _is_nonempty_string(issue.get(field)):
            errors.append(f"{prefix}.{field} 必须是非空字符串")
    for field in (
        "reproduction_steps",
        "evidence_refs",
        "affected_scope",
        "allowed_scope",
        "forbidden_changes",
        "verification_commands",
    ):
        values = issue.get(field)
        if not isinstance(values, list):
            errors.append(f"{prefix}.{field} 必须是列表")
            continue
        if field == "reproduction_steps":
            if not values or not all(_is_nonempty_string(item) for item in values):
                errors.append(f"{prefix}.reproduction_steps 必须包含可执行步骤")
        elif field == "verification_commands":
            if not values or not all(
                isinstance(item, list)
                and item
                and all(_is_nonempty_string(part) for part in item)
                for item in values
            ):
                errors.append(f"{prefix}.verification_commands 必须是非空参数数组列表")
        else:
            for path_index, path in enumerate(values):
                path_error = validate_relative_path(path)
                if path_error:
                    errors.append(f"{prefix}.{field}[{path_index}] {path_error}")
    traceability = issue.get("traceability_status")
    if traceability not in TRACEABILITY_STATUSES:
        errors.append(f"{prefix}.traceability_status 枚举无效")
    if issue.get("category") in FUNCTIONAL_CATEGORIES and traceability == "MAPPED":
        for field in ("requirement_id", "acceptance_criterion_id"):
            if not _is_nonempty_string(issue.get(field)):
                errors.append(f"{prefix}.{field} 在 MAPPED 时必填")
    if traceability in {"UNMAPPED", "PARTIALLY_MAPPED"} and not _is_nonempty_string(
        issue.get("traceability_reason")
    ):
        errors.append(f"{prefix}.traceability_reason 在未完整映射时必填")
    if issue.get("category") == "evidence_missing":
        if issue.get("evidence_missing_reason") not in EVIDENCE_MISSING_ROUTES:
            errors.append(f"{prefix}.evidence_missing_reason 枚举无效")
    if issue.get("category") == "reference_evidence_missing":
        if issue.get("reference_evidence_missing_reason") not in EVIDENCE_MISSING_ROUTES:
            errors.append(f"{prefix}.reference_evidence_missing_reason 枚举无效")
    if str(issue.get("category", "")).startswith("reference_"):
        if not _is_nonempty_string(issue.get("reference_decision_id")):
            errors.append(f"{prefix}.reference_decision_id 缺失")
    if issue.get("category") == "unauthorized_change":
        if issue.get("severity") != "blocker" or issue.get("blocking") is not True:
            errors.append(f"{prefix} 未授权修改必须是 blocking blocker")
    if issue.get("severity") in {"blocker", "critical"} and issue.get("blocking") is not True:
        errors.append(f"{prefix} blocker/critical 必须 blocking=true")
    if issue.get("severity") == "observation" and issue.get("blocking") is not False:
        errors.append(f"{prefix} observation 必须 blocking=false")
    try:
        expected_route, _ = _issue_route(issue)
        if issue.get("route_to") != expected_route:
            errors.append(f"{prefix}.route_to 与确定性路由不一致")
    except ProjectStateError as exc:
        errors.append(f"{prefix}：{exc}")
    return errors


def validate_issue_package(
    package: dict[str, Any],
    *,
    filename: str | Path | None = None,
    markdown_reference: str | None = None,
) -> list[str]:
    """校验完整 Issue Package，并交叉检查摘要、编号、报告引用和路由。"""
    errors: list[str] = []
    evaluation_id = package.get("evaluation_id")
    if package.get("schema_version") != "1.0":
        errors.append("schema_version 必须为字符串 1.0")
    if not isinstance(evaluation_id, str) or not EVALUATION_PATTERN.fullmatch(evaluation_id):
        return errors + ["evaluation_id 格式必须为 evaluation-<三位编号>"]
    if filename is not None and Path(filename).name != f"{evaluation_id}.yaml":
        errors.append("文件名与 evaluation_id 不一致")
    if package.get("result") not in RESULTS:
        errors.append("result 枚举无效")
    if not _is_nonempty_string(package.get("project_id")):
        errors.append("project_id 必须是非空字符串")
    if not _valid_timestamp(package.get("created_at")):
        errors.append("created_at 必须是带时区的有效 ISO-8601 时间")
    if not isinstance(package.get("current_iteration"), int) or not 0 <= package["current_iteration"] <= 5:
        errors.append("current_iteration 必须位于 0..5")
    report_ref = package.get("report_reference")
    expected_report = f"evaluation/reports/{evaluation_id}.md"
    if report_ref != expected_report:
        errors.append("report_reference 与 evaluation_id 不一致")
    if markdown_reference is not None and markdown_reference != report_ref:
        errors.append("Markdown 报告与 Issue Package 互相引用不一致")
    issues = package.get("issues")
    if not isinstance(issues, list):
        return errors + ["issues 必须是列表"]
    seen: set[str] = set()
    for index, item in enumerate(issues):
        if not isinstance(item, dict):
            errors.append(f"issues[{index}] 必须是对象")
            continue
        errors.extend(_validate_issue(item, evaluation_id, index))
        current_id = item.get("issue_id")
        if isinstance(current_id, str):
            if current_id in seen:
                errors.append(f"重复 issue_id：{current_id}")
            seen.add(current_id)
    summary = package.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary 必须是对象")
    else:
        blocking = sum(
            1
            for item in issues
            if isinstance(item, dict)
            and item.get("blocking") is True
            and item.get("status") in {"OPEN", "REOPENED"}
        )
        expected = {
            "total_issues": len(issues),
            "blocking_issues": blocking,
            "non_blocking_issues": len(issues) - blocking,
        }
        for field, value in expected.items():
            if summary.get(field) != value:
                errors.append(f"summary.{field} 与 issues 实际数量不一致")
    if not errors:
        route = resolve_issue_route(issues)
        if package.get("return_to") != route["target"]:
            errors.append("return_to 与 Issue 路由优先级不一致")
        expected_result = "PASS" if route["target"] == "ACCEPTED" else (
            "BLOCKED" if route["next_status"] == "BLOCKED" else "FAIL"
        )
        if package.get("result") != expected_result:
            errors.append("result 与开放 Issue 的确定性路由不一致")
    return errors


def _project_file(
    project_root: str | Path,
    reference: str | Path,
) -> Path:
    root = Path(project_root).resolve()
    candidate = Path(reference)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectStateError("结构化工件路径逃出项目根目录") from exc
    return resolved


def load_issue_package(
    project_root: str | Path,
    reference: str | Path,
) -> dict[str, Any]:
    """使用无构造器的安全 YAML 子集解析器读取 Issue Package。"""
    path = _project_file(project_root, reference)
    package = parse_project_yaml(path.read_text(encoding="utf-8"))
    errors = validate_issue_package(package, filename=path)
    if errors:
        raise ProjectStateError("Issue Package 无效：" + "; ".join(errors))
    return package


def _response_map(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("issue_id"): item
        for item in response.get("issue_responses", [])
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }


def validate_generator_response(
    response: dict[str, Any],
    source_package: dict[str, Any],
) -> list[str]:
    """逐项校验 Generator 回应；blocking/critical 不得遗漏。"""
    errors: list[str] = []
    if response.get("schema_version") != "1.0":
        errors.append("Generator Response schema_version 必须为字符串 1.0")
    source_evaluation = response.get("source_evaluation")
    if source_evaluation != source_package.get("evaluation_id"):
        errors.append("source_evaluation 与 Issue Package 不匹配")
    if not HANDOFF_PATTERN.fullmatch(str(response.get("generator_handoff_id", ""))):
        errors.append("generator_handoff_id 格式无效")
    if not RESPONSE_REFERENCE_PATTERN.fullmatch(str(response.get("response_reference", ""))):
        errors.append("response_reference 必须位于 memory/handoffs/responses/")
    if not _valid_timestamp(response.get("created_at")):
        errors.append("created_at 必须是带时区的有效 ISO-8601 时间")
    source_issues = {
        item["issue_id"]: item
        for item in source_package.get("issues", [])
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }
    responses = response.get("issue_responses")
    if not isinstance(responses, list):
        return errors + ["issue_responses 必须是列表"]
    seen: set[str] = set()
    for index, item in enumerate(responses):
        prefix = f"issue_responses[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        current_id = item.get("issue_id")
        if current_id not in source_issues:
            errors.append(f"{prefix}.issue_id 未出现在来源 Evaluation")
        if current_id in seen:
            errors.append(f"{prefix}.issue_id 重复")
        if isinstance(current_id, str):
            seen.add(current_id)
        status = item.get("status")
        if status not in RESPONSE_STATUSES:
            errors.append(f"{prefix}.status 枚举无效")
            continue
        if status == "FIXED":
            for field in ("changed_files", "verification_commands", "verification_results"):
                if not isinstance(item.get(field), list) or not item[field]:
                    errors.append(f"{prefix}.{field} 在 FIXED 时必填")
            if not _is_nonempty_string(item.get("explanation")):
                errors.append(f"{prefix}.explanation 在 FIXED 时必填")
        elif status == "PARTIALLY_FIXED":
            for field in ("completed", "remaining"):
                if not _is_nonempty_string(item.get(field)):
                    errors.append(f"{prefix}.{field} 在 PARTIALLY_FIXED 时必填")
        elif status == "NOT_FIXED" and not _is_nonempty_string(item.get("reason")):
            errors.append(f"{prefix}.reason 在 NOT_FIXED 时必填")
        elif status == "NEEDS_CLARIFICATION":
            if not _is_nonempty_string(item.get("reason")):
                errors.append(f"{prefix}.reason 在 NEEDS_CLARIFICATION 时必填")
            if item.get("requested_route") not in {"PLANNER", "USER"}:
                errors.append(f"{prefix}.requested_route 枚举无效")
        elif status == "CANNOT_REPRODUCE":
            for field in ("environment", "reproduction_steps", "observed_result"):
                if not item.get(field):
                    errors.append(f"{prefix}.{field} 在 CANNOT_REPRODUCE 时必填")
        elif status == "OUT_OF_SCOPE" and not _is_nonempty_string(item.get("scope_reference")):
            errors.append(f"{prefix}.scope_reference 在 OUT_OF_SCOPE 时必填")
        for field in ("changed_files",):
            for path_index, path in enumerate(item.get(field) or []):
                path_error = validate_relative_path(path)
                if path_error:
                    errors.append(f"{prefix}.{field}[{path_index}] {path_error}")
        commands = item.get("verification_commands") or []
        results = item.get("verification_results") or []
        if commands or results:
            command_keys = {
                json.dumps(command, ensure_ascii=False)
                for command in commands
                if isinstance(command, list)
            }
            result_keys = {
                json.dumps(result.get("command"), ensure_ascii=False)
                for result in results
                if isinstance(result, dict) and isinstance(result.get("command"), list)
            }
            if command_keys != result_keys:
                errors.append(f"{prefix}.verification_results 与命令不一一对应")
            if any(
                not isinstance(result, dict)
                or not isinstance(result.get("exit_code"), int)
                for result in results
            ):
                errors.append(f"{prefix}.verification_results 缺少整数 exit_code")
    for current_id, issue in source_issues.items():
        if (
            issue.get("status") in {"OPEN", "REOPENED"}
            and (issue.get("blocking") is True or issue.get("severity") == "critical")
            and current_id not in seen
        ):
            errors.append(f"未回应 blocking/critical Issue：{current_id}")
    return errors


def write_generator_response_atomic(
    project_root: str | Path,
    response: dict[str, Any],
    source_package: dict[str, Any],
) -> Path:
    """校验后追加写入 Generator Response，拒绝覆盖历史回应。"""
    errors = validate_generator_response(response, source_package)
    if errors:
        raise ProjectStateError("Generator Response 无效：" + "; ".join(errors))
    target = _project_file(project_root, response["response_reference"])
    if target.exists():
        raise ProjectStateError("Generator Response 已存在，禁止覆盖")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".yaml.tmp")
    temporary.write_text(serialize_project_state(response), encoding="utf-8")
    temporary.replace(target)
    return target


def load_generator_response(
    project_root: str | Path,
    reference: str | Path,
    source_package: dict[str, Any],
) -> dict[str, Any]:
    path = _project_file(project_root, reference)
    response = parse_project_yaml(path.read_text(encoding="utf-8"))
    errors = validate_generator_response(response, source_package)
    if errors:
        raise ProjectStateError("Generator Response 无效：" + "; ".join(errors))
    return response


def build_recheck_records(
    previous_package: dict[str, Any],
    response: dict[str, Any],
    outcomes: dict[str, bool],
) -> list[dict[str, Any]]:
    """根据实际复验结果更新生命周期；Generator 声明不能替代复验。"""
    errors = validate_generator_response(response, previous_package)
    if errors:
        raise ProjectStateError("Generator Response 无效：" + "; ".join(errors))
    responses = _response_map(response)
    records: list[dict[str, Any]] = []
    for issue in previous_package.get("issues", []):
        current_id = issue["issue_id"]
        if current_id not in outcomes:
            continue
        passed = outcomes[current_id]
        previous_status = issue.get("status", "OPEN")
        if passed:
            current_result = "RESOLVED"
        elif previous_status == "RESOLVED":
            current_result = "REOPENED"
        else:
            current_result = "OPEN"
        records.append(
            {
                "issue_id": current_id,
                "previous_status": previous_status,
                "generator_response": responses.get(current_id, {}).get("status"),
                "current_result": current_result,
            }
        )
    return records


def evaluate_pass_policy(
    package: dict[str, Any],
    *,
    required_acceptance_criteria_checked: bool,
    required_generator_handoff_present: bool,
    required_evidence_complete: bool,
    protected_artifacts_unchanged: bool,
    prior_blocking_issues_answered: bool = True,
) -> tuple[bool, list[str]]:
    """硬 Gate 优先于总分；观察项不单独阻止 PASS。"""
    reasons: list[str] = []
    open_issues = [
        item
        for item in package.get("issues", [])
        if item.get("status") in {"OPEN", "REOPENED"}
    ]
    if any(item.get("severity") == "blocker" for item in open_issues):
        reasons.append("存在开放 blocker")
    if any(item.get("severity") == "critical" for item in open_issues):
        reasons.append("存在开放 critical")
    if not required_acceptance_criteria_checked:
        reasons.append("必需验收标准尚未全部检查")
    if not required_generator_handoff_present:
        reasons.append("缺少必需 Generator handoff")
    if not required_evidence_complete:
        reasons.append("必需证据不完整")
    if not protected_artifacts_unchanged:
        reasons.append("受保护工件存在未授权修改")
    if not prior_blocking_issues_answered:
        reasons.append("上轮 blocking Issue 未逐项回应")
    return not reasons, reasons


def render_evaluation_markdown(package: dict[str, Any]) -> str:
    """由结构化事实生成保留的人类可读 Markdown 报告。"""
    lines = [
        f"# 验收报告 {package['evaluation_id']}",
        "",
        "## 评估对象",
        "",
        f"- 项目：`{package['project_id']}`",
        f"- 结构化问题：`evaluation/issues/{package['evaluation_id']}.yaml`",
        f"- 结果：`{package['result']}`",
        "",
        "## 必需检查与证据",
        "",
    ]
    if not package["issues"]:
        lines.append("- 未发现开放问题。")
    for item in package["issues"]:
        lines.extend(
            [
                f"- `{item['issue_id']}` [{item['severity']}] {item['title']}",
                f"  - 分类：`{item['category']}`",
                f"  - 路由：`{item['route_to']}`",
                f"  - 证据：{', '.join(f'`{ref}`' for ref in item['evidence_refs']) or '无'}",
            ]
        )
    reference_section = package.get("reference_conformance")
    if isinstance(reference_section, dict):
        lines.extend(
            [
                "",
                "## Reference Conformance",
                "",
                f"- Gate：`{reference_section.get('result', 'NOT_EVALUATED')}`",
                f"- Contract：`{reference_section.get('contract_id', 'N/A')}`",
                f"- Contract Hash：`{reference_section.get('contract_hash', 'N/A')}`",
            ]
        )
        for binding in reference_section.get("binding_results", []):
            if isinstance(binding, dict):
                lines.append(
                    f"- `{binding.get('reference_decision_id')}`：{binding.get('result')}，"
                    f"类型 `{binding.get('conformance_type')}`，证据 {', '.join(binding.get('evidence_refs', [])) or '缺失'}"
                )
    lines.extend(
        [
            "",
            "## 评分",
            "",
            "- 评分不覆盖硬性 PASS Gate。",
            "",
            "## 问题与路由",
            "",
            f"- 返回对象：`{package['return_to']}`",
            "",
            "## 最终结论",
            "",
            package["result"],
            "",
        ]
    )
    return "\n".join(lines)


def commit_evaluation_transaction(
    project_root: str | Path,
    package: dict[str, Any],
    next_state: dict[str, Any],
    *,
    fail_at: str | None = None,
    state_writer: Callable[[str | Path, dict[str, Any]], None] = write_project_state_atomic,
) -> dict[str, str]:
    """先校验并暂存报告/Issue，最后原子更新 project.yaml。

    `fail_at` 仅用于失败注入测试。状态写入失败时保留完整报告、Issue 和恢复日志，
    但不会伪造已更新的 project.yaml。
    """
    root = Path(project_root).resolve()
    evaluation_id = package.get("evaluation_id")
    if not isinstance(evaluation_id, str) or not EVALUATION_PATTERN.fullmatch(evaluation_id):
        raise ProjectStateError("evaluation_id 格式无效")
    issue_path = root / "evaluation" / "issues" / f"{evaluation_id}.yaml"
    report_path = root / "evaluation" / "reports" / f"{evaluation_id}.md"
    transaction_dir = root / "evaluation" / ".transactions" / evaluation_id
    journal_path = transaction_dir / "journal.json"
    if issue_path.exists() or report_path.exists():
        raise ProjectStateError("Evaluation 历史工件已存在，禁止覆盖")
    errors = validate_issue_package(
        package,
        filename=issue_path,
        markdown_reference=package.get("report_reference"),
    )
    if errors:
        raise ProjectStateError("Issue Package 无效：" + "; ".join(errors))
    transaction_dir.mkdir(parents=True, exist_ok=False)
    issue_temp = transaction_dir / "issues.yaml.tmp"
    report_temp = transaction_dir / "report.md.tmp"
    state_temp = transaction_dir / "project-state.yaml.tmp"
    state_payload = deepcopy(next_state)
    state_payload["last_evaluation"] = report_path.relative_to(root).as_posix()
    state_payload["last_issue_package"] = issue_path.relative_to(root).as_posix()
    journal = {
        "evaluation_id": evaluation_id,
        "status": "STAGING",
        "issue_path": issue_path.relative_to(root).as_posix(),
        "report_path": report_path.relative_to(root).as_posix(),
        "state_path": "project.yaml",
        "staged_state_path": state_temp.relative_to(root).as_posix(),
    }
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    issue_temp.write_text(serialize_project_state(package), encoding="utf-8")
    if fail_at == "issue_write":
        raise OSError("注入 Issue 写入失败")
    report_temp.write_text(render_evaluation_markdown(package), encoding="utf-8")
    state_temp.write_text(serialize_project_state(state_payload), encoding="utf-8")
    if fail_at == "report_write":
        raise OSError("注入 Markdown 写入失败")
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    issue_temp.replace(issue_path)
    report_temp.replace(report_path)
    journal["status"] = "ARTIFACTS_COMMITTED"
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if fail_at == "state_write":
        journal["status"] = "RECOVERY_REQUIRED"
        journal_path.write_text(
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise OSError("注入状态写入失败")
    try:
        state_writer(root / "project.yaml", state_payload)
    except Exception:
        journal["status"] = "RECOVERY_REQUIRED"
        journal_path.write_text(
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    journal["status"] = "COMMITTED"
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "issue": issue_path.relative_to(root).as_posix(),
        "report": report_path.relative_to(root).as_posix(),
        "journal": journal_path.relative_to(root).as_posix(),
    }


def recover_evaluation_transaction(
    project_root: str | Path,
    evaluation_id: str,
    *,
    state_writer: Callable[[str | Path, dict[str, Any]], None] = write_project_state_atomic,
) -> dict[str, str]:
    """幂等恢复 `RECOVERY_REQUIRED` Evaluation 事务。

    若 project.yaml 已包含目标引用，只补记 COMMITTED；否则使用事务中持久化的
    完整候选状态再次提交。调用方可注入受 Lease/CAS 约束的 state_writer。
    """

    root = Path(project_root).resolve()
    transaction_dir = root / "evaluation" / ".transactions" / evaluation_id
    journal_path = transaction_dir / "journal.json"
    if not journal_path.is_file():
        raise ProjectStateError("Evaluation 恢复日志不存在")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("evaluation_id") != evaluation_id:
        raise ProjectStateError("Evaluation 恢复日志关联错误")
    if journal.get("status") == "COMMITTED":
        return {"result": "IDEMPOTENT", "journal": str(journal_path)}
    if journal.get("status") != "RECOVERY_REQUIRED":
        raise ProjectStateError(f"事务状态不可恢复：{journal.get('status')}")
    issue_reference = str(journal["issue_path"])
    report_reference = str(journal["report_path"])
    if not (root / issue_reference).is_file() or not (root / report_reference).is_file():
        raise ProjectStateError("Evaluation 已提交工件缺失，拒绝伪造恢复")
    current = load_project_state(root / "project.yaml")
    if (
        current.get("last_evaluation") != report_reference
        or current.get("last_issue_package") != issue_reference
    ):
        staged_path = root / str(journal["staged_state_path"])
        if not staged_path.is_file():
            raise ProjectStateError("Evaluation 恢复候选状态缺失")
        staged = parse_project_yaml(staged_path.read_text(encoding="utf-8"))
        state_writer(root / "project.yaml", staged)
    journal["status"] = "COMMITTED"
    temporary = journal_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(journal_path)
    return {"result": "RECOVERED", "journal": str(journal_path)}
