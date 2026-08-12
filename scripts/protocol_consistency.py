"""跨配置、Runtime、Prompt 和文档的协议一致性检查器。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE_ROLES = frozenset({"planner", "generator", "evaluator"})
FORMAL_MODULES = frozenset(
    {"first_ask_intake", "domain_research", "reference_analysis", "change_request"}
)
WAIT_STATES = frozenset(
    {
        "WAITING_FOR_REQUIREMENTS",
        "WAITING_FOR_DESIGN_REVIEW",
        "WAITING_FOR_PRODUCT_REVIEW",
        "WAITING_FOR_PLAN_REVIEW",
        "WAITING_FOR_CHANGE_APPROVAL",
        "WAITING_FOR_USER",
    }
)
NON_STATE_MARKERS = frozenset(
    {
        "ACCEPTED",
        "ARCHIVED",
        "BLOCKED",
        "PASS",
        "FAIL",
        "MVP",
        "UI",
        "UX",
        "AC",
        "QA",
        "E2E",
        "HTML",
        "CSS",
        "URL",
        "YAML",
        "JSON",
        "API",
        "CAS",
        "F10",
        "F11",
        "F12",
        "F13",
        "F14",
        "FRESH_INVOCATION",
        "REAL_MODEL",
        "REAL_BROWSER",
        "CONTROLLED_RUNTIME",
        "CONTROLLED_QUALIFIED",
        "GLOBAL_READY",
        "GLOBAL_ENABLED",
        "FALLBACK_F13",
        "PYTHON_ONLY",
        "LLM_REQUIRED",
        "IMPLEMENTED",
        "UNAVAILABLE_FROM_HOST",
        "EVALUATOR_INDEPENDENT",
        "EVALUATOR_REPRODUCED",
        "RUNTIME_VERIFIED",
        "NOT_APPLICABLE",
        "CANNOT_REPRODUCE",
        "OUT_OF_SCOPE",
        "GENERATOR_REVIEWED",
        "PARTIALLY_MAPPED",
        "PARTIALLY_FIXED",
        "NOT_FIXED",
        "NEEDS_CLARIFICATION",
        "RECOVERY_REQUIRED",
        "RUNTIME_CONTEXT_ONLY",
        "RUNTIME_HISTORY_MISSING",
        "UNKNOWN_AFTER_CRASH",
        "PROJECT_STATE_CONFLICT",
        "CHILD_THREAD",
        "ROLE_EXECUTION_FALLBACK",
        "HOST_CHILD_THREAD_UNAVAILABLE",
        "HOST_UNAVAILABLE",
    }
)
DOC_ROOTS = ("README.md", "SKILL.md", "intake", "prompts", "docs")
DOC_EXCLUDED_PARTS = {"reports"}
VERSION_RE = re.compile(r"(?:workflow|工作流)\s*v(\d+)|schema\s*v(\d+)", re.I)
STATE_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`|\b(?:status|target_status|source_status):\s*([A-Z][A-Z0-9_]+)")
F14_MARKER_RE = re.compile(
    r"<!--\s*protocol:\s*f14-production-status\s+([^>]+?)\s*-->", re.I
)


@dataclass
class ConsistencyReport:
    """机器检查结果；错误列表非空时 check 必须失败。"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors

    def ok(self, name: str) -> None:
        self.checks.append(name)

    def error(self, code: str, detail: str) -> None:
        self.errors.append(f"{code}: {detail}")

    def warning(self, code: str, detail: str) -> None:
        self.warnings.append(f"{code}: {detail}")


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法解析 {path}: {exc}") from exc


def load_documents(root: Path = ROOT) -> dict[str, Any]:
    """读取 manifest 与其声明的配置；不读取任何 managed project。"""

    manifest_path = root / "config" / "protocol_manifest.yaml"
    manifest = _load_yaml(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("protocol_manifest.yaml 顶层必须是映射")
    sources = manifest.get("authoritative_sources")
    if not isinstance(sources, dict):
        raise ValueError("protocol_manifest.yaml 缺少 authoritative_sources")
    documents: dict[str, Any] = {"protocol_manifest.yaml": manifest}
    for name, relative in sources.items():
        if not isinstance(relative, str):
            raise ValueError(f"Authority 路径无效：{name}")
        documents[name] = _load_yaml(root / relative)
    documents["runtime.yaml"] = _load_yaml(root / "config" / "runtime.yaml")
    documents["change_request.yaml"] = _load_yaml(root / "config" / "change_request.yaml")
    return documents


def load_document_texts(root: Path = ROOT) -> dict[str, str]:
    """读取当前协议说明；历史 reports 不参与当前 Authority 检查。"""

    paths: list[Path] = []
    for entry in DOC_ROOTS:
        path = root / entry
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(path.rglob("*.md"))
    result: dict[str, str] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if any(part in DOC_EXCLUDED_PARTS for part in Path(relative).parts):
            continue
        try:
            result[relative] = path.read_text(encoding="utf-8")
        except OSError as exc:
            result[relative] = f"__READ_ERROR__:{exc}"
    return result


def _mapping(value: Any, name: str, report: ConsistencyReport) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        report.error("STRUCTURE_INVALID", f"{name} 必须是映射")
        return {}
    return value


def _check_versions(
    documents: Mapping[str, Any], report: ConsistencyReport, *, root: Path
) -> None:
    manifest = _mapping(documents.get("protocol_manifest.yaml"), "manifest", report)
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    role_policy = _mapping(documents.get("role_policy"), "role_policy", report)
    runtime = _mapping(documents.get("runtime.yaml"), "runtime", report)
    expected_protocol = manifest.get("protocol_version")
    expected_schema = manifest.get("project_schema_version")
    if expected_protocol != workflow.get("version"):
        report.error("PROTOCOL_VERSION_DRIFT", "manifest 与 workflow.version 不一致")
    if expected_protocol != role_policy.get("version"):
        report.error("PROTOCOL_VERSION_DRIFT", "manifest 与 role_policy.version 不一致")
    runtime_schema = _mapping(runtime.get("project_state"), "runtime.project_state", report).get(
        "schema_version"
    )
    workflow_runtime = _mapping(workflow.get("runtime"), "workflow.runtime", report).get(
        "project_state_projection_schema"
    )
    if expected_schema != runtime_schema or expected_schema != workflow_runtime:
        report.error("SCHEMA_VERSION_DRIFT", "manifest、workflow.runtime 和 runtime.yaml 不一致")
    schema_path = root / "config" / "schemas" / "project_v7.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.error("SCHEMA_AUTHORITY_UNAVAILABLE", str(exc))
    else:
        # v7 是对 v6 的 runtime extension；版本事实由项目模板和继承链共同表达。
        template = _load_yaml(root / "templates" / "project.yaml")
        inherits = schema.get("inherits")
        extension = schema.get("runtimeExtension")
        if inherits != "project_v6.schema.json" or not isinstance(extension, Mapping):
            report.error("SCHEMA_VERSION_DRIFT", "project_v7.schema.json 的继承式 Runtime 扩展损坏")
        elif not isinstance(template, Mapping) or template.get("schema_version") != expected_schema:
            report.error("SCHEMA_VERSION_DRIFT", "templates/project.yaml 与 manifest 不一致")
        else:
            report.ok("schema_version")
    if expected_protocol == workflow.get("version") == role_policy.get("version"):
        report.ok("protocol_version")


def _check_workflow(
    documents: Mapping[str, Any], report: ConsistencyReport, *, root: Path
) -> None:
    manifest = _mapping(documents.get("protocol_manifest.yaml"), "manifest", report)
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    role_policy = _mapping(documents.get("role_policy"), "role_policy", report)
    states = _mapping(workflow.get("states"), "workflow.states", report)
    transitions = _mapping(workflow.get("state_transitions"), "workflow.state_transitions", report)
    aliases = _mapping(workflow.get("legacy_state_aliases"), "workflow.legacy_state_aliases", report)
    modules = tuple(manifest.get("modules") or ())
    if set(manifest.get("core_roles") or ()) != CORE_ROLES:
        report.error("CORE_ROLE_DRIFT", "core_roles 必须严格是 Planner、Generator、Evaluator")
    configured_roles = _mapping(role_policy.get("roles"), "role_policy.roles", report)
    if set(configured_roles) != CORE_ROLES:
        report.error("CORE_ROLE_DRIFT", "role_policies.roles 不得注册第四个 Agent")
    if set(modules) != FORMAL_MODULES:
        report.error("MODULE_DRIFT", "manifest.modules 与正式 Module 集合不一致")
    allowed_modules = _mapping(role_policy.get("module_authorization"), "module_authorization", report).get(
        "allowed_modules", []
    )
    if set(allowed_modules) != FORMAL_MODULES:
        report.error("MODULE_DRIFT", "module_authorization 未覆盖正式 Module 集合")
    for status, route in states.items():
        if not isinstance(route, Mapping):
            report.error("STATE_ROUTE_INVALID", f"{status} 路由不是映射")
            continue
        next_role = route.get("next_role")
        active_module = route.get("active_module")
        if next_role is not None and next_role not in CORE_ROLES:
            report.error("NEXT_ROLE_INVALID", f"{status}.next_role={next_role}")
        if active_module is not None and active_module not in FORMAL_MODULES:
            report.error("ACTIVE_MODULE_INVALID", f"{status}.active_module={active_module}")
        allowed = route.get("allowed_active_modules", [])
        if allowed is None:
            allowed = []
        if not isinstance(allowed, list) or any(item not in FORMAL_MODULES for item in allowed):
            report.error("ACTIVE_MODULE_INVALID", f"{status}.allowed_active_modules 无效")
        if status in WAIT_STATES and not route.get("wait_for_user", False):
            report.error("WAIT_ROUTE_INVALID", f"{status} 未声明 wait_for_user")
    formal_states = set(states) - set(aliases)
    if set(transitions) != formal_states:
        report.error("STATE_TRANSITION_COVERAGE", "state_transitions 未覆盖全部正式状态")
    for source, targets in transitions.items():
        if not isinstance(targets, list):
            report.error("STATE_TRANSITION_INVALID", f"{source} 迁移目标不是列表")
            continue
        for target in targets:
            if target not in states:
                report.error("STATE_TRANSITION_INVALID", f"{source} 指向未知状态 {target}")
    guards = _mapping(workflow.get("state_guards"), "workflow.state_guards", report)
    if not formal_states.issubset(set(guards) | set(aliases)):
        report.error("STATE_GUARD_COVERAGE", "正式状态缺少 state_guard")
    for alias, target in aliases.items():
        route = _mapping(states.get(alias), f"legacy state {alias}", report)
        if not route.get("legacy_read_only") or route.get("migrates_to") != target:
            report.error("LEGACY_STATE_UNMARKED", f"{alias} 必须声明 legacy_read_only 和 migrates_to")
    if set(aliases) and workflow.get("legacy_state_policy") != "read_only_require_migration":
        report.error("LEGACY_POLICY_INVALID", "legacy state 必须只读并要求迁移")
    route_authority = _mapping(manifest.get("runtime_authority"), "runtime_authority", report)
    if route_authority.get("route_loader") != "runtime/policy.py:load_runtime_routes":
        report.error("RUNTIME_AUTHORITY_INVALID", "route_loader 未指向正式 Runtime")
    report.ok("state_routes")


def _check_modules_and_waits(
    documents: Mapping[str, Any], report: ConsistencyReport, *, root: Path
) -> None:
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    role_policy = _mapping(documents.get("role_policy"), "role_policy", report)
    states = _mapping(workflow.get("states"), "workflow.states", report)
    auth = _mapping(role_policy.get("module_authorization"), "module_authorization", report)
    allowed_sources = _mapping(auth.get("allowed_state_sources"), "allowed_state_sources", report)
    for status, route in states.items():
        module = route.get("active_module") if isinstance(route, Mapping) else None
        if module and status not in set(allowed_sources.get(module, [])):
            report.error("MODULE_ROUTE_UNAUTHORIZED", f"{module} 未授权读取状态 {status}")
    role_selector = root / "runtime" / "role_selector.py"
    try:
        source = role_selector.read_text(encoding="utf-8")
    except OSError as exc:
        report.error("RUNTIME_ROUTE_UNAVAILABLE", str(exc))
    else:
        if 'route["wait_for_user"]' not in source or 'Selection("WAIT"' not in source:
            report.error("WAIT_ROLE_BYPASS", "role_selector 未在 wait_for_user 分支阻止 Role")
        else:
            report.ok("wait_states_never_launch_role")


def _check_required_routes(
    documents: Mapping[str, Any], report: ConsistencyReport, *, root: Path
) -> None:
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    transitions = _mapping(workflow.get("state_transitions"), "state_transitions", report)
    route_contract = _mapping(
        _mapping(documents.get("protocol_manifest.yaml"), "manifest", report).get("route_contract"),
        "route_contract",
        report,
    )
    expected = _mapping(route_contract.get("requirements_discovery"), "requirements_discovery", report)
    required = expected.get("research_gate_required")
    decisions = expected.get("research_execution_decisions")
    if required is not True or set(decisions or ()) != {"required", "optional", "not_required"}:
        report.error("RESEARCH_CONTRACT_INVALID", "manifest 未表达 Research Gate 与执行决策")
    if expected.get("research_entry_state") != "REQUIREMENT_RESEARCH":
        report.error("RESEARCH_CONTRACT_INVALID", "Research entry state 必须是 REQUIREMENT_RESEARCH")
    if expected.get("research_return_state") != "INTAKE":
        report.error("RESEARCH_CONTRACT_INVALID", "Research return state 必须是 INTAKE")
    if expected.get("reference_analysis_optional") is not True:
        report.error("RESEARCH_CONTRACT_INVALID", "Reference Analysis 必须明确为 optional")
    if expected.get("reference_analysis_state") != "REFERENCE_ANALYSIS":
        report.error("RESEARCH_CONTRACT_INVALID", "Reference Analysis state 契约无效")
    if expected.get("planning_target") != "PLANNING":
        report.error("RESEARCH_CONTRACT_INVALID", "Research planning target 必须是 PLANNING")
    if "REQUIREMENT_RESEARCH" not in transitions.get("INTAKE", []):
        report.error("RESEARCH_ROUTE_DRIFT", "INTAKE 未覆盖 REQUIREMENT_RESEARCH")
    if "INTAKE" not in transitions.get("REQUIREMENT_RESEARCH", []):
        report.error("RESEARCH_ROUTE_DRIFT", "REQUIREMENT_RESEARCH 未返回 Intake Gate")
    if "REFERENCE_ANALYSIS" not in transitions.get("INTAKE", []):
        report.error("REFERENCE_ROUTE_DRIFT", "Intake 未覆盖 REFERENCE_ANALYSIS")
    if "PLANNING" not in transitions.get("REFERENCE_ANALYSIS", []):
        report.error("REFERENCE_ROUTE_DRIFT", "REFERENCE_ANALYSIS 未返回 PLANNING")
    else:
        report.ok("research_reference_planning_route")


def _check_approval_and_governance(
    documents: Mapping[str, Any],
    texts: Mapping[str, str],
    report: ConsistencyReport,
    *,
    root: Path,
) -> None:
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    states = _mapping(workflow.get("states"), "workflow.states", report)
    guards = _mapping(workflow.get("state_guards"), "workflow.state_guards", report)
    for name in (
        "WAITING_FOR_DESIGN_REVIEW",
        "WAITING_FOR_PRODUCT_REVIEW",
        "WAITING_FOR_PLAN_REVIEW",
    ):
        if name not in states:
            report.error("APPROVAL_GATE_MISSING", f"缺少独立门禁状态 {name}")
    product_guard = _mapping(guards.get("WAITING_FOR_PRODUCT_REVIEW"), "product guard", report)
    plan_guard = _mapping(guards.get("WAITING_FOR_PLAN_REVIEW"), "plan guard", report)
    if product_guard.get("active_plan_must_be_null") is not True:
        report.error("PRODUCT_PLAN_GATE_MERGED", "产品评审前 active_plan 必须为空")
    if plan_guard.get("approved_plan_must_be_null") is not True:
        report.error("PRODUCT_PLAN_GATE_MERGED", "Plan 评审前 approved_plan 必须为空")
    required_plan_fields = {
        "required_approved_proposal",
        "required_product_approval_record",
        "required_active_product_spec",
        "required_active_plan",
    }
    if not required_plan_fields.issubset(plan_guard):
        report.error("PRODUCT_PLAN_GATE_MERGED", "Plan 门禁缺少独立产品批准来源链")
    generator_guard = _mapping(guards.get("APPROVED_FOR_IMPLEMENTATION"), "generator guard", report)
    required_generator_fields = {
        "required_approved_proposal",
        "required_product_approval_record",
        "required_active_product_spec",
        "required_active_plan",
        "required_approved_plan",
        "required_plan_approval_record",
    }
    if not required_generator_fields.issubset(generator_guard):
        report.error("GENERATOR_GATE_DRIFT", "Generator Gate 来源链不完整")
    retry = _mapping(workflow.get("controlled_retry"), "controlled_retry", report)
    retry_rules = _load_yaml(root / "config" / "retry_governance.yaml")
    retry_iteration = _mapping(retry_rules.get("iteration"), "retry_governance.iteration", report)
    if retry.get("rules") != "config/retry_governance.yaml" or retry_iteration.get("maximum_automatic_iterations") != retry.get("maximum_automatic_iterations"):
        report.error("EVALUATOR_RETRY_DRIFT", "Evaluator retry governance 与 workflow 不一致")
    change = _mapping(documents.get("change_request.yaml"), "change_request", report)
    change_states = _mapping(change.get("project_states"), "change_request.project_states", report)
    expected_change = {
        "requested": "CHANGE_REQUESTED",
        "waiting_approval": "WAITING_FOR_CHANGE_APPROVAL",
        "implementing": "IMPLEMENTING",
        "evaluating": "EVALUATING",
        "release_ready": "RELEASE_READY",
    }
    if change_states != expected_change:
        report.error("CHANGE_REQUEST_ROUTE_DRIFT", "Change Request project_states 与 workflow 不一致")
    if "product_approval" not in str(texts) or "plan_approval" not in str(texts):
        report.error("APPROVAL_REFERENCE_MISSING", "当前 Prompt/文档没有同时解释产品批准和 Plan 批准")
    else:
        report.ok("independent_approval_gates")


def _check_document_references(
    texts: Mapping[str, str],
    states: set[str],
    aliases: set[str],
    protocol_version: int,
    report: ConsistencyReport,
    *,
    root: Path,
) -> None:
    known = states | aliases
    for relative, text in texts.items():
        for match in VERSION_RE.finditer(text):
            version = int(match.group(1) or match.group(2))
            line = text.count("\n", 0, match.start()) + 1
            line_text = text.splitlines()[line - 1]
            legacy_context = bool(re.search(r"旧|历史|兼容|迁移|legacy|historical|compatib", line_text, re.I))
            if version != protocol_version and not legacy_context:
                report.error("DOCUMENT_VERSION_DRIFT", f"{relative}:{line} 引用非当前协议 v{version}")
        for match in STATE_TOKEN_RE.finditer(text):
            token = match.group(1) or match.group(2)
            if token in NON_STATE_MARKERS or token in known:
                continue
            line = text.count("\n", 0, match.start()) + 1
            line_text = text.splitlines()[line - 1]
            state_shaped = bool(
                re.search(
                    r"\b(?:status|state|route|transition|next_role|active_module|迁移|状态)\b",
                    line_text,
                    re.I,
                )
            )
            state_prefix = token.startswith(
                (
                    "INTAKE",
                    "WAITING_",
                    "REQUIREMENT_",
                    "REFERENCE_",
                    "PLANNING",
                    "DESIGN_",
                    "APPROVED_",
                    "IMPLEMENTING",
                    "EVALUATING",
                    "CHANGE_",
                    "RELEASE_",
                    "ARCHIVED",
                    "BLOCKED",
                    "ACCEPTED",
                )
            )
            if "_STATE" not in token and not state_shaped and not state_prefix:
                continue
            report.error("DOCUMENT_STATE_REFERENCE_INVALID", f"{relative}:{line} 引用未知状态 {token}")
    report.ok("document_state_references")


def _parse_marker(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in value.split():
        if "=" in token:
            key, item = token.split("=", 1)
            result[key] = item
    return result


def _check_f14(
    documents: Mapping[str, Any],
    texts: Mapping[str, str],
    report: ConsistencyReport,
    *,
    root: Path,
) -> None:
    f14 = _mapping(documents.get("f14"), "f14", report)
    section = _mapping(f14.get("f14"), "f14.f14", report)
    rollout = _mapping(section.get("rollout"), "f14.rollout", report)
    mode = rollout.get("mode")
    qualification = rollout.get("qualification_status")
    global_enabled = rollout.get("global_enabled")
    fallback = rollout.get("fallback_mode")
    evidence = _mapping(section.get("qualification_evidence"), "f14.qualification_evidence", report)
    if mode not in {"controlled", "global", "fallback"}:
        report.error("F14_ROLLOUT_INVALID", "rollout.mode 无效")
    if qualification not in {"IMPLEMENTED", "CONTROLLED_QUALIFIED", "REAL_MODEL_QUALIFIED", "GLOBAL_READY", "GLOBAL_ENABLED", "FALLBACK_F13"}:
        report.error("F14_ROLLOUT_INVALID", "rollout.qualification_status 无效")
    if fallback != "f13_full" or rollout.get("automatic_fallback") is not True:
        report.error("F14_FALLBACK_INVALID", "F13 fallback 必须自动且固定为 f13_full")
    required_evidence = {
        "IMPLEMENTED": (),
        "CONTROLLED_QUALIFIED": ("controlled",),
        "REAL_MODEL_QUALIFIED": ("controlled", "real_model"),
        "GLOBAL_READY": ("controlled", "real_model", "real_browser", "evaluator_selective"),
        "GLOBAL_ENABLED": (
            "controlled",
            "real_model",
            "real_browser",
            "evaluator_selective",
            "quality_parity",
            "fault_injection",
            "fallback",
        ),
        "FALLBACK_F13": ("fallback",),
    }
    for key in required_evidence.get(str(qualification), ()):
        if evidence.get(key) != "PASS":
            report.error("F14_QUALIFICATION_EVIDENCE_MISSING", f"{qualification} 缺少 {key}=PASS 证据")
    selective = _mapping(section.get("selective_context"), "selective_context", report)
    evaluator = _mapping(section.get("evaluator_selective_context"), "evaluator_selective_context", report)
    gate = _mapping(section.get("invocation_gate"), "invocation_gate", report)
    if selective.get("enabled") and (
        qualification in {"IMPLEMENTED", "FALLBACK_F13"}
        or evidence.get("controlled") != "PASS"
    ):
        report.error("F14_FEATURE_GATE_BYPASS", "Selective Context 缺少 controlled=PASS 资格证据")
    if gate.get("enabled") and (
        qualification in {"IMPLEMENTED", "FALLBACK_F13"}
        or evidence.get("controlled") != "PASS"
        or evidence.get("fault_injection") != "PASS"
        or evidence.get("semantic_task_misclassified_as_python_only") != 0
    ):
        report.error("F14_FEATURE_GATE_BYPASS", "Invocation Gate 缺少能力级资格证据")
    evaluator_requirements = ("evaluator_selective", "quality_parity", "real_model", "real_browser")
    if evaluator.get("enabled") and (
        qualification in {"IMPLEMENTED", "FALLBACK_F13"}
        or any(evidence.get(key) != "PASS" for key in evaluator_requirements)
    ):
        report.error("F14_FEATURE_GATE_BYPASS", "Evaluator Selective 缺少独立真实资格证据")
    if global_enabled != (mode == "global" and qualification == "GLOBAL_ENABLED"):
        report.error("F14_GLOBAL_BYPASS", "global_enabled 必须与 GLOBAL_ENABLED 资格状态和 global 模式同时一致")
    if global_enabled:
        if mode != "global" or qualification != "GLOBAL_ENABLED":
            report.error("F14_GLOBAL_BYPASS", "GLOBAL_ENABLED 必须由资格状态和 rollout.mode 同时证明")
        if not selective.get("enabled") or not evaluator.get("enabled") or not gate.get("enabled"):
            report.error("F14_GLOBAL_BYPASS", "全局启用时三项 F14 能力必须全部打开")
    marker_text = texts.get("docs/RUNTIME_CAPABILITY_STATUS.md", "")
    marker_match = F14_MARKER_RE.search(marker_text)
    if not marker_match:
        report.error("F14_STATUS_AUTHORITY_MISSING", "RUNTIME_CAPABILITY_STATUS 缺少机器状态标记")
    else:
        marker = _parse_marker(marker_match.group(1))
        expected = {
            "mode": str(mode),
            "qualification_status": str(qualification),
            "global_enabled": str(bool(global_enabled)).lower(),
            "fallback_mode": str(fallback),
        }
        for key, value in expected.items():
            if marker.get(key) != value:
                report.error("F14_STATUS_DRIFT", f"文档 marker {key}={marker.get(key)}，配置为 {value}")
    if mode == "controlled" and not global_enabled:
        report.ok("f14_not_promoted_without_global_evidence")


def check_documents(
    documents: Mapping[str, Any], texts: Mapping[str, str], *, root: Path = ROOT
) -> ConsistencyReport:
    """对已加载内容执行检查，便于测试用内存故障注入验证 fail closed。"""

    report = ConsistencyReport()
    root = root.resolve()
    _check_versions(documents, report, root=root)
    _check_workflow(documents, report, root=root)
    _check_modules_and_waits(documents, report, root=root)
    _check_required_routes(documents, report, root=root)
    _check_approval_and_governance(documents, texts, report, root=root)
    workflow = _mapping(documents.get("workflow"), "workflow", report)
    manifest = _mapping(documents.get("protocol_manifest.yaml"), "manifest", report)
    _check_document_references(
        texts,
        set(_mapping(workflow.get("states"), "states", report)),
        set(_mapping(workflow.get("legacy_state_aliases"), "aliases", report)),
        int(manifest.get("protocol_version", 0) or 0),
        report,
        root=root,
    )
    _check_f14(documents, texts, report, root=root)
    return report


def run_check(root: Path = ROOT) -> ConsistencyReport:
    try:
        documents = load_documents(root)
        texts = load_document_texts(root)
    except ValueError as exc:
        return ConsistencyReport(errors=[f"LOAD_FAILED: {exc}"])
    return check_documents(documents, texts, root=root)


def _render(report: ConsistencyReport) -> str:
    lines = [
        f"Protocol consistency: {'PASS' if report.passed else 'FAIL'}",
        f"Checks: {len(report.checks)}",
    ]
    if report.errors:
        lines.append("Errors:")
        lines.extend(f"- {item}" for item in report.errors)
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in report.warnings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查协议 Authority 与实现/文档漂移")
    parser.add_argument("command", choices=("check", "report"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    report = run_check(args.root.resolve())
    print(_render(report))
    if args.command == "check" and not report.passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
