"""R6 Reference Conformance 的确定性计划、证据和结果校验。"""

from __future__ import annotations

import re
from typing import Any, Mapping

try:
    from .project_state import ProjectStateError
    from .reference_contract import validate_approved_reference_contract
except ImportError:  # 兼容 tests 直接把 scripts 加入 sys.path
    from project_state import ProjectStateError
    try:
        from scripts.reference_contract import validate_approved_reference_contract
    except ImportError:
        from reference_contract import validate_approved_reference_contract


CONFORMANCE_TYPES = (
    "structural",
    "behavioral",
    "visual",
    "content",
    "technical",
)
CAPABILITY_STATUSES = ("available", "unavailable", "not_required")
CONFORMANCE_RESULTS = ("PASS", "FAIL", "BLOCKED", "UNVERIFIED")
EVIDENCE_TYPES = (
    "static_check",
    "runtime_check",
    "browser_step",
    "screenshot",
    "content_check",
    "technical_check",
)
_REFDEC = re.compile(r"\bREFDEC-[0-9]{3,4}\b")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _nonempty(item)]


def _approved_segment(
    decision_id: str,
    approved_spec_text: str,
    approved_plan_text: str,
) -> str:
    text = f"{approved_spec_text}\n{approved_plan_text}"
    position = text.find(decision_id)
    if position < 0:
        return ""
    return text[max(0, position - 360) : position + 360].casefold()


def infer_conformance_type(
    decision_id: str,
    *,
    approved_spec_text: str = "",
    approved_plan_text: str = "",
) -> str:
    """只根据批准后的 Spec/Plan 推断检查类别，不从原始 Reference 推导要求。"""

    segment = _approved_segment(decision_id, approved_spec_text, approved_plan_text)
    if any(token in segment for token in ("visual", "screenshot", "pixel", "spacing", "typography", "视觉", "截图")):
        return "visual"
    if any(token in segment for token in ("behavior", "interaction", "click", "navigate", "行为", "交互", "点击", "导航")):
        return "behavioral"
    if any(token in segment for token in ("content", "copy", "text", "内容", "文案", "文本")):
        return "content"
    if any(token in segment for token in ("technical", "architecture", "api", "component boundary", "技术", "架构", "组件边界")):
        return "technical"
    return "structural"


def default_capability_matrix() -> dict[str, str]:
    """当前 Runtime 能力矩阵；没有 Vision Provider，因此视觉始终不可用。"""

    return {
        "structural": "available",
        "behavioral": "available",
        "visual": "unavailable",
        "content": "available",
        "technical": "available",
    }


def build_reference_conformance_plan(
    contract: Mapping[str, Any] | None,
    *,
    approved_spec_text: str = "",
    approved_plan_text: str = "",
) -> dict[str, Any]:
    """为每个批准绑定生成可审计的检查计划。"""

    if contract is None:
        return {
            "gate_id": "GATE-REFERENCE-CONFORMANCE",
            "result": "NOT_APPLICABLE",
            "reason": "no_approved_reference_contract",
            "bindings": [],
        }
    try:
        validate_approved_reference_contract(contract)
    except ProjectStateError:
        raise
    bindings = []
    for binding in contract["reference_bindings"]:
        decision_id = str(binding["reference_decision_id"])
        conformance_type = infer_conformance_type(
            decision_id,
            approved_spec_text=approved_spec_text,
            approved_plan_text=approved_plan_text,
        )
        bindings.append(
            {
                "reference_decision_id": decision_id,
                "acceptance_refs": list(binding["acceptance_refs"]),
                "plan_refs": list(binding["plan_refs"]),
                "requirement_refs": list(binding["requirement_refs"]),
                "conformance_type": conformance_type,
                "required_evidence_types": {
                    "structural": ["static_check", "browser_step"],
                    "behavioral": ["browser_step", "runtime_check"],
                    "visual": ["screenshot"],
                    "content": ["content_check", "browser_step"],
                    "technical": ["technical_check", "static_check"],
                }[conformance_type],
                "exclusion_refs": list(binding.get("exclusions", [])),
            }
        )
    return {
        "gate_id": "GATE-REFERENCE-CONFORMANCE",
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "result": "NOT_EVALUATED",
        "bindings": bindings,
    }


def _known_manifest_evidence(manifest: Mapping[str, Any]) -> set[str]:
    known: set[str] = set()
    for field, key in (
        ("commands", "command_id"),
        ("artifacts", "artifact_id"),
        ("checks", "check_id"),
        ("browser_runs", "browser_run_id"),
        ("browser_evidence", "step_id"),
        ("feature_findings", "finding_id"),
        ("feature_observations", "observation_id"),
    ):
        values = manifest.get(field, [])
        if isinstance(values, list):
            known.update(str(item[key]) for item in values if isinstance(item, Mapping) and _nonempty(item.get(key)))
    return known


def _reference_evidence_index(manifest: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    errors: list[str] = []
    records = manifest.get("reference_evidence", [])
    if not isinstance(records, list):
        return {}, ["reference_evidence 必须是列表"]
    index: dict[str, Mapping[str, Any]] = {}
    known = _known_manifest_evidence(manifest)
    for position, record in enumerate(records):
        prefix = f"reference_evidence[{position}]"
        if not isinstance(record, Mapping):
            errors.append(f"{prefix} 必须是对象")
            continue
        evidence_id = record.get("evidence_id")
        if not _nonempty(evidence_id) or evidence_id in index:
            errors.append(f"{prefix}.evidence_id 无效或重复")
            continue
        if record.get("evidence_type") not in EVIDENCE_TYPES:
            errors.append(f"{prefix}.evidence_type 无效")
        if record.get("source") not in {"evaluator", "runtime", "browser", "build_test_regression"}:
            errors.append(f"{prefix}.source 无效")
        if record.get("evaluator_owned") is not True:
            errors.append(f"{prefix}.evaluator_owned 必须为 true")
        refs = record.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not _nonempty(item) for item in refs):
            errors.append(f"{prefix}.evidence_refs 必须是非空列表")
        elif set(refs) - known:
            errors.append(f"{prefix}.evidence_refs 存在未知证据 ID")
        index[str(evidence_id)] = record
    return index, errors


def validate_reference_conformance_section(
    section: Any,
    contract: Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    *,
    approved_spec_text: str = "",
    approved_plan_text: str = "",
    capability_matrix: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    """校验 Evaluator 提交的 Reference Conformance 和独立证据。"""

    if contract is None:
        if section not in (None, {}, {"result": "NOT_APPLICABLE"}):
            return ["无批准 Reference Contract 时不得提交 Reference Conformance"], None
        return [], {
            "gate_id": "GATE-REFERENCE-CONFORMANCE",
            "result": "NOT_APPLICABLE",
            "reason": "no_approved_reference_contract",
            "evidence_refs": [],
            "binding_results": [],
        }
    try:
        validate_approved_reference_contract(contract)
    except ProjectStateError as exc:
        return [str(exc)], None
    if not isinstance(section, Mapping):
        return ["REFERENCE_EVIDENCE_MISSING:reference_conformance"], None
    if section.get("contract_id") != contract["contract_id"] or section.get("contract_hash") != contract["contract_hash"]:
        return ["REFERENCE_STALE_BINDING:contract_hash"], None
    raw_results = section.get("binding_results")
    if not isinstance(raw_results, list):
        return ["REFERENCE_EVIDENCE_MISSING:binding_results"], None
    expected = {str(item["reference_decision_id"]): item for item in contract["reference_bindings"]}
    actual: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    evidence_index, evidence_errors = _reference_evidence_index(manifest)
    errors.extend(evidence_errors)
    matrix = default_capability_matrix()
    if capability_matrix is not None:
        for key, value in capability_matrix.items():
            if key in CONFORMANCE_TYPES and value in CAPABILITY_STATUSES:
                matrix[key] = value
    all_evidence_refs: list[str] = []
    normalized_results: list[dict[str, Any]] = []
    for position, item in enumerate(raw_results):
        prefix = f"binding_results[{position}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} 必须是对象")
            continue
        decision_id = item.get("reference_decision_id")
        if not _nonempty(decision_id) or decision_id not in expected or decision_id in actual:
            errors.append(f"{prefix}.reference_decision_id 无效、重复或不在批准 Contract 中")
            continue
        actual[str(decision_id)] = item
        expected_binding = expected[str(decision_id)]
        conformance_type = infer_conformance_type(
            str(decision_id),
            approved_spec_text=approved_spec_text,
            approved_plan_text=approved_plan_text,
        )
        if item.get("conformance_type") != conformance_type:
            errors.append(f"{prefix}.conformance_type 与批准 Spec/Plan 不一致")
        if _list(item.get("acceptance_refs")) != list(expected_binding["acceptance_refs"]):
            errors.append(f"{prefix}.acceptance_refs 与 Contract 不一致")
        if _list(item.get("plan_refs")) != list(expected_binding["plan_refs"]):
            errors.append(f"{prefix}.plan_refs 与 Contract 不一致")
        result = item.get("result")
        if result not in CONFORMANCE_RESULTS:
            errors.append(f"{prefix}.result 无效")
        capability = matrix.get(conformance_type, "not_required")
        if item.get("capability") != capability:
            errors.append(f"{prefix}.capability 与 Runtime 能力矩阵不一致")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not _nonempty(ref) for ref in refs):
            errors.append(f"{prefix}.evidence_refs 必须是非空列表")
            refs = []
        unknown_refs = [ref for ref in refs if ref not in evidence_index]
        if unknown_refs:
            errors.append(f"{prefix}.evidence_refs 包含未登记的独立证据")
        evidence_types = {str(evidence_index[ref].get("evidence_type")) for ref in refs if ref in evidence_index}
        required_types = set(
            {
                "structural": ("static_check", "browser_step"),
                "behavioral": ("browser_step", "runtime_check"),
                "visual": ("screenshot",),
                "content": ("content_check", "browser_step"),
                "technical": ("technical_check", "static_check"),
            }[conformance_type]
        )
        if capability == "available" and not evidence_types & required_types:
            errors.append(f"REFERENCE_EVIDENCE_MISSING:{decision_id}")
        if capability == "unavailable" and result == "PASS":
            errors.append(f"REFERENCE_CAPABILITY_BLOCKED:{decision_id}")
        if result == "PASS" and (not _nonempty(item.get("expected")) or not _nonempty(item.get("observed"))):
            errors.append(f"{prefix}.expected/observed 缺失")
        exclusion_checks = item.get("exclusion_checks", [])
        if not isinstance(exclusion_checks, list):
            errors.append(f"{prefix}.exclusion_checks 必须是列表")
            exclusion_checks = []
        expected_exclusions = set(_list(expected_binding.get("exclusions")))
        reported_exclusions: set[str] = set()
        for exclusion in exclusion_checks:
            if not isinstance(exclusion, Mapping) or exclusion.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
                errors.append(f"{prefix}.exclusion_checks 存在无效检查")
                continue
            exclusion_ref = exclusion.get("exclusion_ref")
            if not _nonempty(exclusion_ref) or exclusion_ref not in expected_exclusions:
                errors.append(f"{prefix}.exclusion_checks 引用了未批准的 exclusion")
            else:
                reported_exclusions.add(str(exclusion_ref))
        if expected_exclusions - reported_exclusions:
            errors.append(
                "REFERENCE_EVIDENCE_MISSING:exclusions:" + ",".join(sorted(expected_exclusions - reported_exclusions))
            )
        all_evidence_refs.extend(str(ref) for ref in refs)
        normalized_results.append(
            {
                "reference_decision_id": str(decision_id),
                "conformance_type": conformance_type,
                "result": result,
                "capability": capability,
                "acceptance_refs": list(expected_binding["acceptance_refs"]),
                "plan_refs": list(expected_binding["plan_refs"]),
                "evidence_refs": list(refs),
                "exclusion_checks": list(exclusion_checks),
            }
        )
    missing = sorted(set(expected) - set(actual))
    if missing:
        errors.append("REFERENCE_EVIDENCE_MISSING:binding_results:" + ",".join(missing))
    if errors:
        return errors, None
    if any(item["result"] == "BLOCKED" or item["capability"] == "unavailable" for item in normalized_results):
        result = "BLOCKED"
    elif any(item["result"] in {"FAIL", "UNVERIFIED"} for item in normalized_results):
        result = "FAIL"
    else:
        result = "PASS"
    if any(
        exclusion.get("result") in {"FAIL", "BLOCKED"}
        for item in normalized_results
        for exclusion in item["exclusion_checks"]
    ):
        result = "BLOCKED" if any(
            exclusion.get("result") == "BLOCKED"
            for item in normalized_results
            for exclusion in item["exclusion_checks"]
        ) else "FAIL"
    return [], {
        "gate_id": "GATE-REFERENCE-CONFORMANCE",
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "result": result,
        "evidence_refs": sorted(set(all_evidence_refs)),
        "binding_results": normalized_results,
    }


def reference_conformance_issues(
    evaluation_id: str,
    gate: Mapping[str, Any],
    *,
    start_sequence: int = 1,
) -> list[dict[str, Any]]:
    """将 Gate 结果映射为现有 Issue Package 可路由的问题。"""

    if gate.get("result") in {"PASS", "NOT_APPLICABLE"}:
        return []
    issues: list[dict[str, Any]] = []
    for offset, binding in enumerate(gate.get("binding_results", [])):
        if not isinstance(binding, Mapping) or binding.get("result") == "PASS":
            continue
        result = binding.get("result")
        capability = binding.get("capability")
        if binding.get("contract_conflict") is True:
            category = "scope_mismatch"
            route_to, next_status = "PLANNER", "PLANNING"
            severity = "critical"
        elif binding.get("reference_missing") is True:
            category = "reference_missing"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "major"
        elif binding.get("stale_binding") is True:
            category = "reference_stale_binding"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "critical"
        elif binding.get("scope_creep") is True:
            category = "reference_scope_creep"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "critical"
        elif binding.get("evidence_missing_reason") in {"generator_omission", "evaluator_environment", "profile_gap"}:
            category = "reference_evidence_missing"
            reason = binding["evidence_missing_reason"]
            route_to, next_status = {
                "generator_omission": ("GENERATOR", "IMPLEMENTING"),
                "evaluator_environment": ("SYSTEM_OR_USER", "BLOCKED"),
                "profile_gap": ("PLANNER", "PLANNING"),
            }[reason]
            severity = "major" if reason == "generator_omission" else "blocker"
        elif capability == "unavailable" or result == "BLOCKED":
            category = "reference_capability_blocked"
            route_to, next_status = "SYSTEM_OR_USER", "BLOCKED"
            severity = "blocker"
        elif not binding.get("evidence_refs"):
            category = "reference_evidence_missing"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "major"
        elif any(item.get("result") == "FAIL" for item in binding.get("exclusion_checks", []) if isinstance(item, Mapping)):
            category = "reference_exclusion_violation"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "critical"
        else:
            category = "reference_incorrect"
            route_to, next_status = "GENERATOR", "IMPLEMENTING"
            severity = "major"
        issues.append(
            {
                "issue_id": f"EVAL-{evaluation_id.removeprefix('evaluation-')}-{start_sequence + offset:03d}",
                "evaluation_id": evaluation_id,
                "category": category,
                "severity": severity,
                "status": "OPEN",
                "blocking": severity in {"blocker", "critical"},
                "traceability_status": "MAPPED",
                "traceability_reason": None,
                "requirement_id": None,
                "acceptance_criterion_id": (binding.get("acceptance_refs") or [None])[0],
                "title": f"Reference Conformance {binding.get('reference_decision_id')} 未通过",
                "expected_result": "批准 Reference Binding 与实现证据一致",
                "actual_result": f"{result or 'UNVERIFIED'}；能力={capability or 'unknown'}",
                "reproduction_steps": ["读取对应 Reference Conformance 绑定结果和独立证据"],
                "evidence_refs": list(binding.get("evidence_refs") or []),
                "affected_scope": [str(binding.get("reference_decision_id"))],
                "allowed_scope": list(binding.get("acceptance_refs") or []),
                "forbidden_changes": ["修改批准 Product Spec、Plan 或 Reference Contract"],
                "verification_commands": [["python", "-m", "pytest", "-q"]],
                "route_to": route_to,
                "next_status": next_status,
                "reference_decision_id": binding.get("reference_decision_id"),
                "reference_conformance_result": result,
                "reference_evidence_missing_reason": "generator_omission"
                if category == "reference_evidence_missing"
                else None,
            }
        )
    return issues


__all__ = [
    "CAPABILITY_STATUSES",
    "CONFORMANCE_RESULTS",
    "CONFORMANCE_TYPES",
    "EVIDENCE_TYPES",
    "build_reference_conformance_plan",
    "default_capability_matrix",
    "infer_conformance_type",
    "reference_conformance_issues",
    "validate_reference_conformance_section",
]
