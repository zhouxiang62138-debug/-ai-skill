"""F9.2 可复现证据、验收 Gate、受保护工件与安全命令执行。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from evaluation_protocol import (
    EVALUATION_PATTERN,
    ProjectStateError,
    commit_evaluation_transaction,
    issue_id,
    validate_relative_path,
)
from project_state import parse_project_yaml, serialize_project_state
try:
    from reference_conformance import _reference_evidence_index
except ImportError:  # 允许作为 scripts 包导入
    from .reference_conformance import _reference_evidence_index


GATE_ORDER = (
    "GATE-DELIVERY",
    "GATE-BUILD",
    "GATE-TESTS",
    "GATE-REQUIREMENTS",
    "GATE-BROWSER-ACCEPTANCE",
    "GATE-FEATURE-COMPLETENESS",
    "GATE-REFERENCE-CONFORMANCE",
    "GATE-REGRESSION",
    "GATE-NON_FUNCTIONAL",
    "GATE-EVIDENCE",
)
COMMAND_STATUSES = ("PASSED", "FAILED", "BLOCKED", "TIMED_OUT")
CHECK_RESULTS = ("PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE", "NOT_EVALUATED")
GATE_RESULTS = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT_APPLICABLE")
EVIDENCE_PROVENANCE = (
    "GENERATOR_PROVIDED",
    "EVALUATOR_REPRODUCED",
    "RUNTIME_VERIFIED",
    "EXTERNAL",
)
INDEPENDENT_PROVENANCE = {"EVALUATOR_REPRODUCED", "RUNTIME_VERIFIED"}
SENSITIVE_PATTERN = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization)\s*[:=]\s*([^\s]+)"
)
PROHIBITED_EXECUTABLES = {
    "rm",
    "rmdir",
    "del",
    "erase",
    "remove-item",
    "format",
    "shutdown",
}
SHELL_EXECUTABLES = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "sh",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_browser_evidence_records(
    browser_runs: Any,
    browser_evidence: Any,
    evaluation_id: str,
) -> tuple[list[str], set[str], set[str]]:
    """校验 Browser Run/Step，并返回可用于交叉引用的 ID 集合。"""

    errors: list[str] = []
    if not isinstance(browser_runs, list):
        return ["browser_runs 必须是列表"], set(), set()
    if not isinstance(browser_evidence, list):
        return ["browser_evidence 必须是列表"], set(), set()
    run_ids: set[str] = set()
    step_ids: set[str] = set()
    run_map: dict[str, dict[str, Any]] = {}
    for index, run in enumerate(browser_runs):
        prefix = f"browser_runs[{index}]"
        if not isinstance(run, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        run_id = run.get("browser_run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"browser-run-\d{3}", run_id):
            errors.append(f"{prefix}.browser_run_id 格式无效")
        elif run_id in run_ids:
            errors.append(f"重复 browser_run_id：{run_id}")
        else:
            run_ids.add(run_id)
            run_map[run_id] = run
        if run.get("evaluation_id") != evaluation_id:
            errors.append(f"{prefix}.evaluation_id 与 manifest 不一致")
        if run.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
            errors.append(f"{prefix}.result 无效")
        if not isinstance(run.get("evidence_refs"), list):
            errors.append(f"{prefix}.evidence_refs 必须是列表")
        for field in ("started_at", "finished_at"):
            if not _timestamp(run.get(field)):
                errors.append(f"{prefix}.{field} 必须是带时区的 ISO-8601 时间")
    step_map: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(browser_evidence):
        prefix = f"browser_evidence[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not re.fullmatch(
            r"browser-run-\d{3}-step-\d{3}", step_id
        ):
            errors.append(f"{prefix}.step_id 格式无效")
        elif step_id in step_ids:
            errors.append(f"重复 step_id：{step_id}")
        else:
            step_ids.add(step_id)
            step_map[step_id] = step
        if step.get("evaluation_id") != evaluation_id:
            errors.append(f"{prefix}.evaluation_id 与 manifest 不一致")
        if step.get("browser_run_id") not in run_ids:
            errors.append(f"{prefix}.browser_run_id 未知")
        if step.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
            errors.append(f"{prefix}.result 无效")
        for field in (
            "requirement_id",
            "acceptance_criterion_id",
            "action",
            "target",
            "expected",
            "observed",
        ):
            if not _nonempty(step.get(field)):
                errors.append(f"{prefix}.{field} 必须是非空字符串")
        for field in ("console_errors", "network_failures"):
            if not isinstance(step.get(field), list):
                errors.append(f"{prefix}.{field} 必须是列表")
        for field in ("started_at", "finished_at"):
            if not _timestamp(step.get(field)):
                errors.append(f"{prefix}.{field} 必须是带时区的 ISO-8601 时间")
        screenshot = step.get("screenshot_reference")
        if screenshot is not None:
            path_error = validate_relative_path(screenshot)
            if path_error:
                errors.append(f"{prefix}.screenshot_reference {path_error}")
    for run_id, run in run_map.items():
        refs = run.get("evidence_refs")
        if isinstance(refs, list):
            unknown = [item for item in refs if item not in step_ids]
            if unknown:
                errors.append(f"browser_runs[{run_id}].evidence_refs 包含未知 ID")
    for step_id, step in step_map.items():
        run = run_map.get(str(step.get("browser_run_id")))
        if run is not None and step_id not in set(run.get("evidence_refs") or []):
            errors.append(f"{step_id} 未被 Browser Run 引用")
    return errors, run_ids, step_ids


def _validate_feature_completeness_records(
    summary: Any,
    findings: Any,
    observations: Any,
    evaluation_id: str,
) -> tuple[list[str], set[str], set[str]]:
    """校验 Feature Finding/Observation，并返回可引用的 ID 集合。"""

    errors: list[str] = []
    if summary is None and findings == [] and observations == []:
        return errors, set(), set()
    if summary is not None and not isinstance(summary, dict):
        errors.append("feature_completeness 必须是对象")
        summary = {}
    finding_ids: set[str] = set()
    observation_ids: set[str] = set()
    if not isinstance(findings, list):
        errors.append("feature_findings 必须是列表")
        findings = []
    if not isinstance(observations, list):
        errors.append("feature_observations 必须是列表")
        observations = []
    for index, finding in enumerate(findings):
        prefix = f"feature_findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not re.fullmatch(
            r"FC-FIND-\d{3}", finding_id
        ):
            errors.append(f"{prefix}.finding_id 格式无效")
        elif finding_id in finding_ids:
            errors.append(f"重复 finding_id：{finding_id}")
        else:
            finding_ids.add(finding_id)
        if finding.get("severity") not in {"blocker", "critical", "major", "minor", "observation"}:
            errors.append(f"{prefix}.severity 无效")
        if not _nonempty(finding.get("category")) or not _nonempty(finding.get("path")):
            errors.append(f"{prefix}.category/path 必须是非空字符串")
        if not isinstance(finding.get("line"), int) or finding.get("line") <= 0:
            errors.append(f"{prefix}.line 必须是正整数")
        if not _nonempty(finding.get("evidence")):
            errors.append(f"{prefix}.evidence 必须是非空字符串")
        if not isinstance(finding.get("penalty"), (int, float)):
            errors.append(f"{prefix}.penalty 必须是数字")
    for index, observation in enumerate(observations):
        prefix = f"feature_observations[{index}]"
        if not isinstance(observation, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        observation_id = observation.get("observation_id")
        if not isinstance(observation_id, str) or not re.fullmatch(
            r"FC-OBS-\d{3}", observation_id
        ):
            errors.append(f"{prefix}.observation_id 格式无效")
        elif observation_id in observation_ids:
            errors.append(f"重复 observation_id：{observation_id}")
        else:
            observation_ids.add(observation_id)
        if observation.get("evaluation_id", evaluation_id) != evaluation_id:
            errors.append(f"{prefix}.evaluation_id 与 manifest 不一致")
        if observation.get("source") not in {"runtime", "browser", "requirement"}:
            errors.append(f"{prefix}.source 无效")
        if observation.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
            errors.append(f"{prefix}.result 无效")
        for field in (
            "requirement_id",
            "acceptance_criterion_id",
            "expected",
            "observed",
        ):
            if not _nonempty(observation.get(field)):
                errors.append(f"{prefix}.{field} 必须是非空字符串")
        refs = observation.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(_nonempty(item) for item in refs):
            errors.append(f"{prefix}.evidence_refs 必须是列表")
    if isinstance(summary, dict):
        if summary.get("result") not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            errors.append("feature_completeness.result 无效")
        score = summary.get("score")
        if score is not None and (
            not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 10
        ):
            errors.append("feature_completeness.score 无效")
        for field, valid_ids in (
            ("finding_refs", finding_ids),
            ("observation_refs", observation_ids),
        ):
            refs = summary.get(field, [])
            if not isinstance(refs, list):
                errors.append(f"feature_completeness.{field} 必须是列表")
            elif any(item not in valid_ids for item in refs):
                errors.append(f"feature_completeness.{field} 包含未知 ID")
    return errors, finding_ids, observation_ids


def _unique_records(
    records: Any,
    key: str,
    label: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        errors.append(f"{label} 必须是列表")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] 必须是对象")
            continue
        current = item.get(key)
        if not _nonempty(current):
            errors.append(f"{label}[{index}].{key} 必须是非空字符串")
            continue
        if current in result:
            errors.append(f"{label} 存在重复 {key}：{current}")
        result[current] = item
    return result


def evidence_directory(project_root: str | Path, evaluation_id: str) -> Path:
    if not EVALUATION_PATTERN.fullmatch(evaluation_id):
        raise ProjectStateError("evaluation_id 格式无效")
    root = Path(project_root).resolve()
    return root / "evaluation" / "evidence" / evaluation_id


def validate_evidence_manifest(
    manifest: dict[str, Any],
    *,
    filename: str | Path | None = None,
    known_issue_ids: set[str] | None = None,
    known_requirement_ids: set[str] | None = None,
) -> list[str]:
    """执行 Evidence Manifest 的结构与交叉引用校验。"""
    errors: list[str] = []
    if manifest.get("schema_version") != "1.0":
        errors.append("Evidence Manifest schema_version 必须为字符串 1.0")
    evaluation_id = manifest.get("evaluation_id")
    if not isinstance(evaluation_id, str) or not EVALUATION_PATTERN.fullmatch(evaluation_id):
        return errors + ["evaluation_id 格式无效"]
    if filename is not None:
        path = Path(filename)
        if path.name != "manifest.yaml" or path.parent.name != evaluation_id:
            errors.append("manifest 路径必须与 evaluation_id 一致")
    errors.extend(validate_evaluator_independence_manifest(manifest))
    browser_errors, browser_run_ids, browser_step_ids = _validate_browser_evidence_records(
        manifest.get("browser_runs", []),
        manifest.get("browser_evidence", []),
        evaluation_id,
    )
    errors.extend(browser_errors)
    feature_errors, feature_finding_ids, feature_observation_ids = _validate_feature_completeness_records(
        manifest.get("feature_completeness"),
        manifest.get("feature_findings", []),
        manifest.get("feature_observations", []),
        evaluation_id,
    )
    errors.extend(feature_errors)
    if not _timestamp(manifest.get("created_at")):
        errors.append("created_at 必须是带时区的 ISO-8601 时间")
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        errors.append("environment 必须是对象")
    else:
        for field in ("os", "architecture", "python_version", "working_directory"):
            if not _nonempty(environment.get(field)):
                errors.append(f"environment.{field} 必须是非空字符串")
        path_error = validate_relative_path(environment.get("working_directory"))
        if path_error:
            errors.append(f"environment.working_directory {path_error}")
    commands = _unique_records(
        manifest.get("commands"), "command_id", "commands", errors
    )
    artifacts = _unique_records(
        manifest.get("artifacts"), "artifact_id", "artifacts", errors
    )
    checks = _unique_records(manifest.get("checks"), "check_id", "checks", errors)
    gates = _unique_records(manifest.get("gates"), "gate_id", "gates", errors)
    for current_id, command in commands.items():
        prefix = f"commands[{current_id}]"
        if command.get("gate_id") not in GATE_ORDER:
            errors.append(f"{prefix}.gate_id 枚举无效")
        argv = command.get("command")
        if not isinstance(argv, list) or not argv or not all(_nonempty(item) for item in argv):
            errors.append(f"{prefix}.command 必须是非空参数数组")
        for field in ("started_at", "finished_at"):
            if not _timestamp(command.get(field)):
                errors.append(f"{prefix}.{field} 必须是带时区时间")
        if not isinstance(command.get("exit_code"), int) and command.get("status") != "TIMED_OUT":
            errors.append(f"{prefix}.exit_code 必须是整数")
        if command.get("status") not in COMMAND_STATUSES:
            errors.append(f"{prefix}.status 枚举无效")
        if command.get("status") == "PASSED" and command.get("exit_code") != 0:
            errors.append(f"{prefix} PASSED 时 exit_code 必须为 0")
        if command.get("executed_by") not in {"EVALUATOR", "GENERATOR_REVIEWED"}:
            errors.append(f"{prefix}.executed_by 枚举无效")
        if command.get("gate_id") == "GATE-TESTS":
            metrics = command.get("test_metrics")
            if not isinstance(metrics, dict):
                errors.append(f"{prefix}.test_metrics 在测试 Gate 中必填")
            else:
                for field in ("passed", "failed", "skipped"):
                    if not isinstance(metrics.get(field), int) or metrics[field] < 0:
                        errors.append(f"{prefix}.test_metrics.{field} 必须是非负整数")
                if not isinstance(metrics.get("failed_tests"), list):
                    errors.append(f"{prefix}.test_metrics.failed_tests 必须是列表")
                if command.get("status") == "PASSED" and metrics.get("failed") != 0:
                    errors.append(f"{prefix} PASSED 时 failed 测试数必须为 0")
        for field in ("stdout_path", "stderr_path"):
            path_error = validate_relative_path(command.get(field))
            if path_error:
                errors.append(f"{prefix}.{field} {path_error}")
    for current_id, artifact in artifacts.items():
        prefix = f"artifacts[{current_id}]"
        if artifact.get("type") not in {
            "log",
            "screenshot",
            "report",
            "snapshot",
            "test_result",
            "other",
        }:
            errors.append(f"{prefix}.type 枚举无效")
        path_error = validate_relative_path(artifact.get("path"))
        if path_error:
            errors.append(f"{prefix}.path {path_error}")
        for field in ("linked_issue_ids", "linked_requirement_ids"):
            if not isinstance(artifact.get(field), list):
                errors.append(f"{prefix}.{field} 必须是列表")
        if known_issue_ids is not None:
            unknown_issues = sorted(
                set(artifact.get("linked_issue_ids") or []) - known_issue_ids
            )
            if unknown_issues:
                errors.append(f"{prefix}.linked_issue_ids 存在未知 ID：{unknown_issues}")
        if known_requirement_ids is not None:
            unknown_requirements = sorted(
                set(artifact.get("linked_requirement_ids") or [])
                - known_requirement_ids
            )
            if unknown_requirements:
                errors.append(
                    f"{prefix}.linked_requirement_ids 存在未知 ID：{unknown_requirements}"
                )
    known_evidence = (
        set(commands)
        | set(artifacts)
        | browser_run_ids
        | browser_step_ids
        | feature_finding_ids
        | feature_observation_ids
    )
    reference_evidence_index, reference_evidence_errors = _reference_evidence_index(manifest)
    errors.extend(reference_evidence_errors)
    known_evidence |= set(reference_evidence_index)
    for current_id, check in checks.items():
        prefix = f"checks[{current_id}]"
        if check.get("gate_id") not in GATE_ORDER:
            errors.append(f"{prefix}.gate_id 枚举无效")
        if check.get("result") not in CHECK_RESULTS:
            errors.append(f"{prefix}.result 枚举无效")
        refs = check.get("evidence_refs")
        if not isinstance(refs, list):
            errors.append(f"{prefix}.evidence_refs 必须是列表")
        else:
            unknown = sorted(set(refs) - known_evidence)
            if unknown:
                errors.append(f"{prefix}.evidence_refs 存在未知 ID：{unknown}")
        if check.get("required") is True and check.get("result") == "PASS" and not refs:
            errors.append(f"{prefix} 必需 PASS 检查缺少证据")
    gate_sequence = [item.get("gate_id") for item in manifest.get("gates", []) if isinstance(item, dict)]
    expected_sequence = [item for item in GATE_ORDER if item in gate_sequence]
    if gate_sequence != expected_sequence:
        errors.append("gates 未按固定顺序记录")
    for current_id, gate in gates.items():
        prefix = f"gates[{current_id}]"
        if current_id not in GATE_ORDER:
            errors.append(f"{prefix}.gate_id 枚举无效")
        if gate.get("result") not in GATE_RESULTS:
            errors.append(f"{prefix}.result 枚举无效")
        if not isinstance(gate.get("required"), bool):
            errors.append(f"{prefix}.required 必须是布尔值")
        refs = gate.get("evidence_refs")
        if not isinstance(refs, list):
            errors.append(f"{prefix}.evidence_refs 必须是列表")
        elif gate.get("required") and gate.get("result") == "PASS" and not refs:
            errors.append(f"{prefix} 必需 PASS Gate 缺少证据")
        if gate.get("result") == "SKIPPED" and not _nonempty(gate.get("skip_reason")):
            errors.append(f"{prefix} 跳过时必须说明原因")
        if gate.get("result") == "NOT_APPLICABLE" and not _nonempty(gate.get("reason")):
            errors.append(f"{prefix} N/A 时必须说明适用性原因")
    reference_section = manifest.get("reference_conformance")
    if reference_section is not None:
        if not isinstance(reference_section, dict):
            errors.append("reference_conformance 必须是对象")
        else:
            if reference_section.get("result") not in {"PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE", "NOT_EVALUATED"}:
                errors.append("reference_conformance.result 枚举无效")
            if reference_section.get("result") != "NOT_APPLICABLE":
                if not isinstance(reference_section.get("contract_id"), str) or not isinstance(reference_section.get("contract_hash"), str):
                    errors.append("reference_conformance 缺少 Contract 身份")
                if not isinstance(reference_section.get("binding_results"), list):
                    errors.append("reference_conformance.binding_results 必须是列表")
    return errors


def validate_evaluator_independence_manifest(manifest: dict[str, Any]) -> list[str]:
    """校验 E1 Manifest 的 provenance 与当前 Invocation 身份绑定。"""

    section = manifest.get("evaluator_independence")
    if section is None:
        return []
    errors: list[str] = []
    if not isinstance(section, dict):
        return ["evaluator_independence 必须是对象"]
    for field in ("invocation_id", "context_manifest_id", "code_snapshot_hash"):
        if not _nonempty(section.get(field)):
            errors.append(f"evaluator_independence.{field} 必须是非空字符串")
    revision = section.get("project_revision")
    if not isinstance(revision, int) or revision < 0:
        errors.append("evaluator_independence.project_revision 必须是非负整数")
    snapshot = section.get("code_snapshot_hash")
    if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
        errors.append("evaluator_independence.code_snapshot_hash 必须是 SHA-256")
    evidence = section.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return errors + ["evaluator_independence.evidence 必须是非空列表"]
    criteria: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(evidence):
        prefix = f"evaluator_independence.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        criterion = item.get("acceptance_criterion_id")
        if not _nonempty(criterion):
            errors.append(f"{prefix}.acceptance_criterion_id 必填")
        if item.get("provenance") not in EVIDENCE_PROVENANCE:
            errors.append(f"{prefix}.provenance 枚举无效")
        for field in ("tool_call_id", "attempt_id", "result_hash", "code_snapshot_hash"):
            if not _nonempty(item.get(field)):
                errors.append(f"{prefix}.{field} 必须是非空字符串")
        if not isinstance(item.get("result_hash"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", str(item.get("result_hash"))
        ):
            errors.append(f"{prefix}.result_hash 必须是 SHA-256")
        if item.get("project_revision") != revision:
            errors.append(f"{prefix}.project_revision 与 Manifest 不一致")
        if item.get("code_snapshot_hash") != snapshot:
            errors.append(f"{prefix}.code_snapshot_hash 与 Manifest 不一致")
        if not _timestamp(item.get("timestamp")):
            errors.append(f"{prefix}.timestamp 必须是带时区时间")
        if not isinstance(item.get("command"), list) or not item.get("command"):
            errors.append(f"{prefix}.command 必须是非空数组")
        if not isinstance(item.get("environment"), dict):
            errors.append(f"{prefix}.environment 必须是对象")
        if _nonempty(criterion):
            criteria.setdefault(str(criterion), []).append(item)
    required_criteria = section.get("required_acceptance_criteria")
    if not isinstance(required_criteria, list) or not required_criteria:
        errors.append("evaluator_independence.required_acceptance_criteria 必须是非空列表")
    else:
        for criterion in required_criteria:
            if not _nonempty(criterion) or str(criterion) not in criteria:
                errors.append(f"required Acceptance Criterion 缺少独立 Evidence：{criterion}")
        for criterion, items in criteria.items():
            if not any(
                item.get("result") == "PASS"
                and item.get("provenance") in INDEPENDENT_PROVENANCE
                for item in items
            ):
                errors.append(f"Acceptance Criterion {criterion} 缺少 Evaluator/Runtime PASS Evidence")
    return errors


def capture_environment(working_directory: str = ".") -> dict[str, str]:
    """只记录可复核的非敏感环境元数据。"""
    return {
        "os": platform.system().lower(),
        "architecture": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "working_directory": working_directory,
    }


def redact_sensitive_output(value: str, limit: int) -> tuple[str, bool]:
    redacted = SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}=<REDACTED>", value)
    encoded = redacted.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return redacted, False
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return clipped + "\n<OUTPUT_TRUNCATED>\n", True


def _command_allowed(command: list[str], allowed_prefixes: Iterable[Iterable[str]]) -> bool:
    lowered = Path(command[0]).name.lower()
    if lowered in PROHIBITED_EXECUTABLES or lowered in SHELL_EXECUTABLES:
        return False
    for prefix in allowed_prefixes:
        expected = list(prefix)
        if not expected:
            continue
        actual_executable = Path(command[0]).name.lower()
        expected_executable = Path(expected[0]).name.lower()
        for suffix in (".exe", ".cmd", ".bat"):
            actual_executable = actual_executable.removesuffix(suffix)
            expected_executable = expected_executable.removesuffix(suffix)
        if (
            actual_executable == expected_executable
            and command[1 : len(expected)] == expected[1:]
        ):
            return True
    return False


def _inside_project(project_root: Path, candidate: str | Path) -> Path:
    path = Path(candidate)
    if not path.is_absolute():
        path = project_root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ProjectStateError("命令 cwd 逃出项目根目录") from exc
    return resolved


def run_verified_command(
    project_root: str | Path,
    evaluation_id: str,
    command_id: str,
    gate_id: str,
    command: list[str],
    *,
    allowed_prefixes: Iterable[Iterable[str]],
    cwd: str | Path = ".",
    timeout_seconds: float = 60.0,
    output_limit_bytes: int = 1_000_000,
    test_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """以参数数组运行白名单命令，保存截断和脱敏后的 stdout/stderr。"""
    root = Path(project_root).resolve()
    if not re.fullmatch(r"CMD-\d{3}", command_id):
        raise ProjectStateError("command_id 格式必须为 CMD-<三位编号>")
    if gate_id not in GATE_ORDER:
        raise ProjectStateError("gate_id 枚举无效")
    if (
        not isinstance(command, list)
        or not command
        or not all(_nonempty(item) for item in command)
    ):
        raise ProjectStateError("命令必须是非空参数数组")
    if not _command_allowed(command, allowed_prefixes):
        raise ProjectStateError("命令不在 Profile 白名单或属于禁止的 shell/删除命令")
    if gate_id == "GATE-TESTS" and not isinstance(test_metrics, dict):
        raise ProjectStateError("测试 Gate 命令必须提供结构化 test_metrics")
    working = _inside_project(root, cwd)
    evidence_root = evidence_directory(root, evaluation_id)
    logs = evidence_root / "commands"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{command_id}.stdout.log"
    stderr_path = logs / f"{command_id}.stderr.log"
    if stdout_path.exists() or stderr_path.exists():
        raise ProjectStateError("命令证据已存在，禁止覆盖")
    started_at = utc_now()
    status = "BLOCKED"
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=working,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
            },
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        status = "PASSED" if exit_code == 0 else "FAILED"
    except subprocess.TimeoutExpired as exc:
        status = "TIMED_OUT"
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr += f"\n命令在 {timeout_seconds} 秒后超时。"
    except OSError as exc:
        status = "BLOCKED"
        stderr = f"命令无法执行：{exc}"
    finished_at = utc_now()
    stdout, stdout_truncated = redact_sensitive_output(stdout, output_limit_bytes)
    stderr, stderr_truncated = redact_sensitive_output(stderr, output_limit_bytes)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    record = {
        "command_id": command_id,
        "gate_id": gate_id,
        "command": command,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "stdout_path": stdout_path.relative_to(root).as_posix(),
        "stderr_path": stderr_path.relative_to(root).as_posix(),
        "status": status,
        "executed_by": "EVALUATOR",
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if gate_id == "GATE-TESTS":
        record["test_metrics"] = test_metrics
    return record


def evaluate_gates(
    gate_inputs: dict[str, dict[str, Any]],
    gate_policy: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按固定顺序计算 Gate；必需 Gate 不允许无理由跳过。"""
    configured = {item["id"]: item for item in gate_policy}
    unknown = set(configured) - set(GATE_ORDER)
    if unknown:
        raise ProjectStateError(f"Gate 配置包含未知 ID：{sorted(unknown)}")
    results: list[dict[str, Any]] = []
    for gate_id in GATE_ORDER:
        policy = configured.get(gate_id)
        if policy is None:
            continue
        required = bool(policy.get("required"))
        current = gate_inputs.get(gate_id)
        if current is None:
            if gate_id == "GATE-REFERENCE-CONFORMANCE":
                result = "NOT_APPLICABLE"
                reason = "no_approved_reference_contract"
            else:
                result = "FAIL" if required else "SKIPPED"
                reason = "required_gate_not_executed" if required else "not_configured_for_project"
            evidence_refs: list[str] = []
        else:
            result = current.get("result")
            reason = current.get("reason")
            evidence_refs = list(current.get("evidence_refs") or [])
            if result not in GATE_RESULTS:
                raise ProjectStateError(f"{gate_id} 结果枚举无效")
            if result == "SKIPPED" and required:
                result = "BLOCKED" if current.get("blocked") else "FAIL"
                reason = reason or "required_gate_cannot_be_skipped"
            if result == "NOT_APPLICABLE" and required:
                result = "FAIL"
                reason = reason or "required_gate_not_applicable"
            if result == "PASS" and required and not evidence_refs:
                result = "FAIL"
                reason = "required_gate_missing_evidence"
        results.append(
            {
                "gate_id": gate_id,
                "required": required,
                "result": result,
                "evidence_refs": evidence_refs,
                "skip_reason": reason if result == "SKIPPED" else None,
                "reason": reason,
            }
        )
    return results


def required_gates_passed(gates: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    failures = [
        f"{item.get('gate_id')}={item.get('result')}"
        for item in gates
        if item.get("required") and item.get("result") != "PASS"
    ]
    return not failures, failures


def validate_acceptance_coverage(
    required_acceptance_criterion_ids: list[str],
    checks: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """必需验收项必须有非 NOT_EVALUATED 的唯一结果。"""
    results = {
        item.get("acceptance_criterion_id"): item.get("result")
        for item in checks
        if isinstance(item, dict)
    }
    missing = [
        current_id
        for current_id in required_acceptance_criterion_ids
        if results.get(current_id) in {None, "NOT_EVALUATED"}
    ]
    return not missing, missing


def validate_delivery_handoff(
    handoff: dict[str, Any],
    *,
    previous_issue_package: dict[str, Any] | None = None,
) -> list[str]:
    required = (
        "implementation_summary",
        "changed_files",
        "test_results",
        "known_limitations",
        "unfinished_items",
        "run_instructions",
        "handoff_id",
    )
    errors = [
        f"handoff.{field} 缺失"
        for field in required
        if field not in handoff or handoff[field] in (None, "")
    ]
    if previous_issue_package is not None and not handoff.get(
        "generator_response_reference"
    ):
        errors.append("返工 handoff 缺少 generator_response_reference")
    return errors


def _protected_files(root: Path, references: Iterable[str]) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for reference in references:
        path_error = validate_relative_path(reference)
        if path_error:
            raise ProjectStateError(f"受保护路径 {reference!r} {path_error}")
        candidate = (root / reference).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ProjectStateError("受保护路径逃出项目根目录") from exc
        if candidate.is_file():
            items = [candidate]
        elif candidate.is_dir():
            items = [item for item in candidate.rglob("*") if item.is_file()]
        else:
            items = []
        for item in items:
            resolved = item.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ProjectStateError("受保护路径包含逃逸的链接") from exc
            relative = item.relative_to(root).as_posix()
            manifest[relative] = hashlib.sha256(item.read_bytes()).hexdigest()
    return dict(sorted(manifest.items()))


def build_protected_snapshot(
    project_root: str | Path,
    protected_paths: Iterable[str],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    return {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "protected_paths": list(protected_paths),
        "files": _protected_files(root, protected_paths),
    }


def compare_protected_snapshot(
    project_root: str | Path,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    paths = snapshot.get("protected_paths")
    before = snapshot.get("files")
    if not isinstance(paths, list) or not isinstance(before, dict):
        raise ProjectStateError("受保护快照格式无效")
    current = _protected_files(root, paths)
    missing = sorted(set(before) - set(current))
    added = sorted(set(current) - set(before))
    modified = sorted(
        key for key in set(before) & set(current) if before[key] != current[key]
    )
    return {
        "unchanged": not (missing or added or modified),
        "missing": missing,
        "added": added,
        "modified": modified,
    }


def unauthorized_change_issue(
    evaluation_id: str,
    sequence: int,
    comparison: dict[str, Any],
) -> dict[str, Any]:
    affected = sorted(
        set(comparison.get("missing", []))
        | set(comparison.get("added", []))
        | set(comparison.get("modified", []))
    )
    return {
        "issue_id": issue_id(evaluation_id, sequence),
        "category": "unauthorized_change",
        "severity": "blocker",
        "title": "检测到受保护工件的未授权修改",
        "requirement_id": None,
        "acceptance_criterion_id": None,
        "traceability_status": "NOT_APPLICABLE",
        "traceability_reason": "权限边界检查，不对应单一产品需求",
        "expected_result": "受保护工件与验收前基线一致",
        "actual_result": json.dumps(comparison, ensure_ascii=False, sort_keys=True),
        "reproduction_steps": ["重新计算受保护路径 SHA-256 清单", "与验收前快照比较"],
        "evidence_refs": [],
        "affected_scope": affected,
        "allowed_scope": affected,
        "forbidden_changes": affected,
        "verification_commands": [["internal", "compare-protected-snapshot"]],
        "blocking": True,
        "status": "OPEN",
        "route_to": "GENERATOR",
    }


def evidence_missing_issue(
    evaluation_id: str,
    sequence: int,
    *,
    reason: str,
    title: str,
    affected_scope: list[str] | None = None,
) -> dict[str, Any]:
    routes = {
        "generator_omission": "GENERATOR",
        "evaluator_environment": "SYSTEM_OR_USER",
        "profile_gap": "PLANNER",
    }
    if reason not in routes:
        raise ProjectStateError("证据缺失原因枚举无效")
    return {
        "issue_id": issue_id(evaluation_id, sequence),
        "category": "evidence_missing",
        "severity": "blocker",
        "title": title,
        "requirement_id": None,
        "acceptance_criterion_id": None,
        "traceability_status": "NOT_APPLICABLE",
        "traceability_reason": "证据基础设施问题",
        "expected_result": "必需检查具有可复核证据",
        "actual_result": "必需证据缺失",
        "reproduction_steps": ["检查 Evidence Manifest 与必需 Gate 映射"],
        "evidence_refs": [],
        "affected_scope": affected_scope or [],
        "allowed_scope": affected_scope or [],
        "forbidden_changes": [],
        "verification_commands": [["internal", "validate-evidence-manifest"]],
        "blocking": True,
        "status": "OPEN",
        "route_to": routes[reason],
        "evidence_missing_reason": reason,
    }


def write_manifest_atomic(
    project_root: str | Path,
    manifest: dict[str, Any],
    *,
    issue_package: dict[str, Any] | None = None,
    known_requirement_ids: set[str] | None = None,
) -> Path:
    root = Path(project_root).resolve()
    evaluation_id = manifest.get("evaluation_id")
    target = evidence_directory(root, evaluation_id) / "manifest.yaml"
    if target.exists():
        raise ProjectStateError("Evidence Manifest 已存在，禁止覆盖")
    errors = validate_evidence_manifest(
        manifest,
        filename=target,
        known_issue_ids=(
            {
                item["issue_id"]
                for item in issue_package.get("issues", [])
                if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
            }
            if issue_package is not None
            else None
        ),
        known_requirement_ids=known_requirement_ids,
    )
    if errors:
        raise ProjectStateError("Evidence Manifest 无效：" + "; ".join(errors))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".yaml.tmp")
    temporary.write_text(serialize_project_state(manifest), encoding="utf-8")
    temporary.replace(target)
    return target


def load_evidence_manifest(
    project_root: str | Path,
    reference: str | Path,
    *,
    issue_package: dict[str, Any] | None = None,
    known_requirement_ids: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = Path(reference)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProjectStateError("Evidence Manifest 路径逃出项目根目录") from exc
    manifest = parse_project_yaml(path.read_text(encoding="utf-8"))
    errors = validate_evidence_manifest(
        manifest,
        filename=path,
        known_issue_ids=(
            {
                item["issue_id"]
                for item in issue_package.get("issues", [])
                if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
            }
            if issue_package is not None
            else None
        ),
        known_requirement_ids=known_requirement_ids,
    )
    if errors:
        raise ProjectStateError("Evidence Manifest 无效：" + "; ".join(errors))
    return manifest


def commit_reproducible_evaluation(
    project_root: str | Path,
    issue_package: dict[str, Any],
    manifest: dict[str, Any],
    next_state: dict[str, Any],
    *,
    state_writer=None,
) -> dict[str, str]:
    """先提交完整 Manifest，再提交 Issue/Markdown，最后写项目状态。"""
    root = Path(project_root).resolve()
    manifest_path = write_manifest_atomic(
        root, manifest, issue_package=issue_package
    )
    state = dict(next_state)
    state["evidence_manifest"] = manifest_path.relative_to(root).as_posix()
    kwargs = {}
    if state_writer is not None:
        kwargs["state_writer"] = state_writer
    result = commit_evaluation_transaction(root, issue_package, state, **kwargs)
    result["manifest"] = manifest_path.relative_to(root).as_posix()
    return result
