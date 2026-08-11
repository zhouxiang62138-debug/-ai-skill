"""Research-Guided Adaptive Requirement Discovery 的纯确定性核心。

这里不执行联网、不写项目文件、不调用模型。它把输入快照转换成可序列化的
分析结果，供 Intake Module、Research Adapter 和 Runtime CAS 使用。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


_STATUS_READY = frozenset({"answered", "assumed", "undecided", "not_applicable"})
_STATUS_DUPLICATE = frozenset({"answered", "assumed", "undecided", "not_applicable"})
_STATUS_REASK = frozenset({"unanswered", "conflicting", "requires_user_decision"})
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_IMPACT_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_DOMAIN_SIGNALS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("sales_analytics", ("销售", "sales", "销售数据", "销售分析"), "analytics_dashboard", "high"),
    ("inventory_management", ("库存", "仓库", "inventory", "warehouse"), "professional_business_system", "high"),
    ("finance_accounting", ("记账", "财务", "会计", "finance", "accounting"), "professional_business_system", "high"),
    ("crm", ("crm", "客户关系", "客户管理"), "professional_business_system", "high"),
    ("erp", ("erp", "企业资源"), "professional_business_system", "high"),
    ("healthcare", ("医疗", "病历", "healthcare", "clinical"), "regulated_business_system", "regulated"),
    ("education", ("教育", "课程", "school", "education"), "professional_business_system", "medium"),
    ("ecommerce", ("电商", "购物车", "商品", "ecommerce"), "professional_business_system", "medium"),
    ("logistics", ("物流", "配送", "logistics"), "professional_business_system", "high"),
    ("金融", ("金融", "银行", "支付", "financial", "banking", "payment"), "regulated_business_system", "regulated"),
)

_SIMPLE_SIGNALS = ("倒计时", "计时器", "单用途", "简单页面", "简单网页", "小 demo", "小demo", "simple timer", "countdown")
_RESEARCH_REQUEST_SIGNALS = ("最佳实践", "行业标准", "行业规范", "benchmark", "对标行业", "research", "研究一下", "参考成熟产品")
_MULTI_USER_SIGNALS = ("多人", "团队", "协作", "multi-user", "multiplayer", "组织", "角色", "权限")

_DIMENSION_FIELD_MAP = {
    "product_intent": "primary_goal",
    "target_users": "target_users",
    "primary_goal": "primary_goal",
    "primary_use_cases": "use_cases",
    "platform": "platform",
    "core_workflows": "use_cases",
    "required_features": "required_features",
    "optional_features": "optional_features",
    "data_model": "data_sources",
    "persistence_sync": None,
    "authentication_user_model": None,
    "permissions_roles": None,
    "external_integrations": None,
    "import_export": None,
    "business_rules": None,
    "reporting_analytics": None,
    "error_exception_handling": None,
    "security_privacy": None,
    "performance_scale": None,
    "offline_connectivity": None,
    "compatibility": None,
    "deployment_constraints": None,
    "regulatory_domain_constraints": None,
    "design_preferences": "design_preferences",
    "undecided_product_decisions": None,
    "technical_constraints": "technical_constraints",
}

_DIMENSION_TOPICS = {
    "target_users": "user_model",
    "authentication_user_model": "user_model",
    "permissions_roles": "user_model",
    "platform": "platform",
    "offline_connectivity": "platform",
    "compatibility": "platform",
    "data_model": "data",
    "external_integrations": "data",
    "import_export": "data",
    "primary_use_cases": "workflow",
    "core_workflows": "workflow",
    "business_rules": "workflow",
    "required_features": "scope",
    "optional_features": "scope",
    "product_intent": "goal",
    "primary_goal": "goal",
    "technical_constraints": "constraints",
    "security_privacy": "constraints",
    "regulatory_domain_constraints": "constraints",
}

_FIELD_LABELS = {
    "target_users": "主要使用者",
    "primary_goal": "核心目标",
    "use_cases": "主要使用场景",
    "platform": "目标平台",
    "required_features": "首版必须功能",
    "data_sources": "数据来源",
    "technical_constraints": "硬性技术或部署限制",
    "permissions_roles": "登录、用户和权限模型",
    "external_integrations": "外部系统集成",
    "core_workflows": "核心工作流",
    "design_preferences": "视觉和布局偏好",
}

_QUESTION_KEYS = {
    "product_intent": "primary_goal.main",
    "target_users": "target_users.primary",
    "primary_goal": "primary_goal.main",
    "primary_use_cases": "use_cases.core",
    "core_workflows": "use_cases.core",
    "platform": "platform.primary",
    "required_features": "required_features.must_have",
    "optional_features": "optional_features.nice_to_have",
    "data_model": "data_sources.primary",
    "technical_constraints": "technical_constraints.hard_limits",
    "design_preferences": "design_preferences.explicit",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _contains_any(text: str, signals: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(signal.casefold() in lowered for signal in signals)


def _epistemic(value: object, status: str, source_ref: str) -> dict[str, Any]:
    return {"value": value, "epistemic_status": status, "source_refs": [source_ref]}


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analyze_initial_intent(
    user_text: str,
    *,
    project_id: str,
    source_request_ref: str,
    intent_id: str = "INTENT-001",
    created_at: str | None = None,
) -> dict[str, Any]:
    """从初始原话提取事实与候选；不会把候选写成 Requirement。"""

    normalized = _text(user_text)
    source = source_request_ref
    matched = [item for item in _DOMAIN_SIGNALS if _contains_any(normalized, item[1])]
    simple = _contains_any(normalized, _SIMPLE_SIGNALS)
    explicit_research = _contains_any(normalized, _RESEARCH_REQUEST_SIGNALS)
    multi_user = _contains_any(normalized, _MULTI_USER_SIGNALS)
    if matched:
        domain, _, category, risk = matched[0]
        domain_status = "candidate"
        category_status = "candidate"
    elif simple:
        domain, category, risk = "general_utility", "single_purpose_tool", "low"
        domain_status = category_status = "inference"
    else:
        domain, category, risk = "unknown", "unknown", "unknown"
        domain_status = category_status = "candidate"

    if risk == "regulated" or _contains_any(normalized, ("合规", "监管", "医疗", "金融")):
        risk_level = "regulated"
    elif risk in {"high", "medium"} or matched:
        risk_level = "high" if risk == "high" else "medium"
    else:
        risk_level = "low" if simple else "unknown"
    complexity = "simple" if simple else "deep" if risk_level in {"high", "regulated"} else "standard"
    maturity = "high" if matched else "low" if simple else "unknown"
    research_value = "high" if explicit_research or matched else "low" if simple else "medium"
    product_type = "dashboard" if _contains_any(normalized, ("dashboard", "报表", "看板", "分析")) else "app" if _contains_any(normalized, ("app", "应用", "系统", "网页", "页面")) else "unknown"
    target_problem = normalized or "unknown"

    workflow_candidates: list[dict[str, Any]] = []
    data_candidates: list[dict[str, Any]] = []
    integration_candidates: list[dict[str, Any]] = []
    nonfunctional_candidates: list[dict[str, Any]] = []
    if matched:
        workflow_candidates.append(_epistemic(f"{domain} 的核心业务工作流", "candidate", source))
        data_candidates.append(_epistemic(f"{domain} 的领域实体与关系", "candidate", source))
    if multi_user:
        workflow_candidates.append(_epistemic("多人协作和角色权限工作流", "candidate", source))
        nonfunctional_candidates.append(_epistemic("认证、授权和审计要求", "candidate", source))
    if _contains_any(normalized, ("excel", "csv", "数据库", "crm", "api", "接口")):
        integration_candidates.append(_epistemic("用户提到的数据接入或外部接口", "candidate", source))
    design_uncertainty = []
    if _contains_any(normalized, ("不确定风格", "风格不确定", "还没想好", "看方案", "参考方案")):
        design_uncertainty.append("visual_style_and_layout")

    known_facts = [_epistemic(normalized, "fact", source)] if normalized else []
    unknown_facts = []
    if not multi_user:
        unknown_facts.append("target_users_and_user_model")
    if not _contains_any(normalized, ("平台", "web", "网页", "移动端", "手机", "desktop", "桌面")):
        unknown_facts.append("platform")
    if not _contains_any(normalized, ("必须", "首版", "核心功能", "功能")):
        unknown_facts.append("required_features_boundary")
    if not _contains_any(normalized, ("数据", "excel", "csv", "数据库", "api")) and not simple:
        unknown_facts.append("data_sources")

    return {
        "schema_version": 1,
        "intent_id": intent_id,
        "project_id": project_id,
        "source_request_ref": source_request_ref,
        "created_at": created_at or _now(),
        "supersedes": None,
        "project_category": _epistemic(category, category_status, source),
        "domain": _epistemic(domain, domain_status, source),
        "likely_product_type": _epistemic(product_type, "inference", source),
        "target_problem": _epistemic(target_problem, "fact" if normalized else "hypothesis", source),
        "known_facts": known_facts,
        "unknown_facts": unknown_facts,
        "likely_complexity": complexity,
        "domain_maturity": maturity,
        "research_value": research_value,
        "risk_level": risk_level,
        "candidates": {
            "core_workflows": workflow_candidates,
            "data_model": data_candidates,
            "integrations": integration_candidates,
            "nonfunctional_requirements": nonfunctional_candidates,
            "design_uncertainty": design_uncertainty,
        },
    }


def evaluate_research_necessity(
    intent: Mapping[str, Any],
    *,
    user_text: str = "",
    requirement_id: str = "RREQ-001",
    created_at: str | None = None,
) -> dict[str, Any]:
    """按显式信号决定研究必要性，不进行网络访问。"""

    normalized = _text(user_text)
    risk = str(intent.get("risk_level") or "unknown")
    category = str((intent.get("project_category") or {}).get("value") or "unknown")
    domain = str((intent.get("domain") or {}).get("value") or "unknown")
    complexity = str(intent.get("likely_complexity") or "standard")
    reasons: list[str] = []
    expected: list[str] = []
    decision = "optional"
    if _contains_any(normalized, _RESEARCH_REQUEST_SIGNALS):
        decision = "required"
        reasons.append("用户明确要求行业研究、最佳实践或 Benchmark")
        expected.append("减少成熟领域的关键工作流遗漏")
    if risk == "regulated":
        decision = "required"
        reasons.append("领域可能涉及监管、医疗或金融风险")
        expected.append("核对最新规范、边界和合规约束")
    if category in {"professional_business_system", "analytics_dashboard", "regulated_business_system"}:
        decision = "required"
        reasons.append("产品类别属于成熟或专业业务系统")
        expected.extend(["发现领域核心工作流", "比较成熟产品的信息架构和异常处理"])
    if complexity == "deep":
        decision = "required"
        reasons.append("初始意图显示复杂数据、业务或集成范围")
        expected.append("降低数据模型、权限和集成设计遗漏")
    if _contains_any(normalized, ("完全原创", "无需 benchmark", "无需研究", "不需要研究")) and risk != "regulated":
        decision = "not_required"
        reasons = ["用户明确要求不进行 Benchmark/外部研究"]
        expected = []
    elif category == "single_purpose_tool" and not _contains_any(normalized, _RESEARCH_REQUEST_SIGNALS):
        decision = "not_required"
        reasons = ["单用途、低复杂度工具通常不需要外部领域研究"]
        expected = []
    elif not reasons:
        reasons.append("领域信号不足以证明研究是硬依赖，但研究可能改善覆盖")
        expected.append("补充领域模式和机会候选")

    profile = "deep" if complexity == "deep" or risk == "regulated" else "simple" if category == "single_purpose_tool" else "standard"
    return {
        "schema_version": 1,
        "requirement_id": requirement_id,
        "project_id": str(intent.get("project_id") or ""),
        "decision": decision,
        "reasons": reasons,
        "target_domains": [domain] if domain != "unknown" else [],
        "expected_value": expected,
        "budget_profile": profile,
        "privacy_redaction": "applied",
        "source_policy": "tiered",
        "input_intent_ref": str(intent.get("intent_id") or "") or None,
        "created_at": created_at or _now(),
        "supersedes": None,
    }


def _field_value(requirements: Mapping[str, Any], field: str | None) -> tuple[Any, str, list[str], Mapping[str, Any]]:
    if not field:
        return None, "unanswered", [], {}
    raw = requirements.get(field)
    if not isinstance(raw, Mapping):
        return None, "unanswered", [], {}
    return raw.get("value"), str(raw.get("status") or "unanswered"), list(raw.get("source_refs") or []), raw


def build_coverage_map(
    requirements: Mapping[str, Any],
    *,
    project_id: str,
    requirements_ref: str,
    coverage_id: str = "COV-001",
    intent: Mapping[str, Any] | None = None,
    research_refs: Iterable[str] = (),
    created_at: str | None = None,
) -> dict[str, Any]:
    """把需求快照投影为覆盖图；缺失字段仍保留为 Gap 候选。"""

    intent = intent or {}
    risk = str(intent.get("risk_level") or "unknown")
    category = str((intent.get("project_category") or {}).get("value") or "unknown")
    simple = category == "single_purpose_tool" or intent.get("likely_complexity") == "simple"
    critical = {"product_intent", "target_users", "primary_goal", "primary_use_cases", "platform", "required_features", "data_model", "technical_constraints"}
    items: list[dict[str, Any]] = []
    for key, field in _DIMENSION_FIELD_MAP.items():
        value, status, source_refs, raw = _field_value(requirements, field)
        if field is None:
            value, status, source_refs = None, "unanswered", []
        if key == "product_intent":
            value, status, source_refs, raw = _field_value(requirements, "primary_goal")
        if key == "data_model":
            value, status, source_refs, raw = _field_value(requirements, "data_sources")
        if key in {"data_model", "persistence_sync"} and simple and not value:
            status = "not_applicable"
        if key == "regulatory_domain_constraints" and risk != "regulated" and not value:
            status = "not_applicable"
        if key == "design_preferences" and status == "undecided":
            # 视觉未决定是合法路由，不当作关键阻塞。
            pass
        if key in {"authentication_user_model", "permissions_roles"} and not _contains_any(_text((intent.get("target_problem") or {}).get("value")), _MULTI_USER_SIGNALS):
            safe = True
        else:
            safe = key not in critical and key not in {"security_privacy", "regulatory_domain_constraints"}
        importance = "critical" if key in critical else "high" if key in {"permissions_roles", "external_integrations", "security_privacy", "regulatory_domain_constraints"} and (risk in {"high", "regulated"} or key == "permissions_roles") else "low" if key in {"design_preferences", "optional_features", "undecided_product_decisions"} else "medium"
        if key == "regulatory_domain_constraints" and risk == "regulated":
            importance = "critical"
        impact = "critical" if key in critical and key in {"product_intent", "primary_goal", "primary_use_cases", "platform", "required_features", "data_model", "technical_constraints"} else "high" if importance in {"critical", "high"} else "low" if importance == "low" else "medium"
        items.append({
            "key": key,
            "value": value,
            "status": status,
            "importance": importance,
            "decision_impact": impact,
            "architecture_impact": "critical" if key in {"platform", "data_model", "permissions_roles", "external_integrations", "security_privacy", "technical_constraints"} else "high" if importance in {"critical", "high"} else "low",
            "workflow_impact": "critical" if key in {"primary_use_cases", "core_workflows", "required_features", "business_rules"} else "high" if importance in {"critical", "high"} else "low",
            "irreversibility": "high" if key in {"platform", "data_model", "permissions_roles", "external_integrations"} else "low",
            "dependency_count": 1 if key in {"data_model", "platform", "permissions_roles", "external_integrations"} else 0,
            "risk": "high" if risk in {"high", "regulated"} and importance in {"critical", "high"} else "low",
            "safe_assumption_available": bool(raw.get("safe_assumption_available", safe)),
            "source_refs": source_refs,
            "research_refs": list(research_refs),
            "last_question_key": raw.get("last_question_key") if isinstance(raw, Mapping) else None,
        })
    return {
        "schema_version": 1,
        "coverage_id": coverage_id,
        "project_id": project_id,
        "requirements_ref": requirements_ref,
        "items": items,
        "created_at": created_at or _now(),
        "supersedes": None,
    }


def analyze_gaps(
    coverage: Mapping[str, Any],
    *,
    project_id: str,
    gap_id: str = "GAP-001",
    created_at: str | None = None,
) -> dict[str, Any]:
    """将 Coverage Item 分成关键阻塞、可提问、可假设和设计路由。"""

    gaps: list[dict[str, Any]] = []
    for item in coverage.get("items", []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "unanswered")
        if status in _STATUS_READY:
            if status == "undecided" and str(item.get("key")) == "design_preferences":
                gaps.append({"gap_key": f"visual:{item['key']}", "field_key": str(item["key"]), "category": "visual_undecided", "priority": "low", "reason": "视觉偏好未决定，交给 Design Exploration", "question_allowed": False, "source_refs": list(item.get("source_refs") or [])})
            continue
        key = str(item.get("key") or "")
        importance = str(item.get("importance") or "medium")
        if key == "design_preferences" and status == "undecided":
            category, priority, allowed, reason = "visual_undecided", "low", False, "视觉偏好未决定，交给 Design Exploration"
        elif importance == "critical" and status == "conflicting":
            category, priority, allowed, reason = "critical_conflicting", "critical", True, "关键产品事实存在冲突，必须由用户消解"
        elif importance == "critical" and status == "requires_user_decision":
            category, priority, allowed, reason = "critical_requires_user_decision", "critical", True, "关键产品事实没有安全默认值"
        elif importance == "critical":
            category, priority, allowed, reason = "critical_unanswered", "critical", True, "关键产品事实尚未处理"
        elif bool(item.get("safe_assumption_available")):
            category, priority, allowed, reason = "safe_assumption_candidate", importance, False, "低风险或可逆，可以记录安全假设"
        else:
            category, priority, allowed, reason = "high_impact_unanswered", "high" if importance == "high" else "medium", True, "该信息会影响产品范围、流程、架构或风险"
        gaps.append({"gap_key": f"{category}:{key}", "field_key": key, "category": category, "priority": priority, "reason": reason, "question_allowed": allowed, "source_refs": list(item.get("source_refs") or [])})
    gaps.sort(key=lambda item: (_PRIORITY_RANK.get(str(item["priority"]), 9), str(item["field_key"])))
    return {"schema_version": 1, "gap_id": gap_id, "project_id": project_id, "coverage_ref": str(coverage.get("coverage_id") or ""), "gaps": gaps, "created_at": created_at or _now(), "supersedes": None}


def _question_text(field_key: str) -> str:
    label = _FIELD_LABELS.get(field_key, field_key)
    if field_key in {"target_users", "permissions_roles", "authentication_user_model"}:
        return f"为了确定{label}，第一版主要给谁使用？是单人使用还是多人/团队协作，是否需要不同角色权限？"
    if field_key in {"platform", "offline_connectivity", "compatibility"}:
        return f"为了确定{label}，第一版准备在哪些设备或平台使用？是否有必须支持的离线或兼容要求？"
    if field_key in {"data_model", "external_integrations", "import_export"}:
        return f"为了确定{label}，数据现在来自哪里、以什么方式进入系统？是否需要连接现有系统或导入导出？"
    if field_key in {"primary_use_cases", "core_workflows", "business_rules"}:
        return f"为了确定{label}，用户完成一次最重要任务时，必须经过哪些关键步骤？"
    if field_key in {"required_features", "optional_features"}:
        return f"为了划定首版范围，哪些能力是没有就无法解决核心问题的，哪些可以先放到后续？"
    if field_key in {"technical_constraints", "security_privacy", "regulatory_domain_constraints"}:
        return f"为了避免后面返工，是否有必须遵守的{_FIELD_LABELS.get(field_key, field_key)}、合规、隐私或部署限制？"
    return f"为了确定产品方向，请补充{label}。"


def prioritize_questions(
    coverage: Mapping[str, Any],
    gaps: Mapping[str, Any],
    *,
    project_id: str,
    round_number: int,
    history_question_keys: Iterable[str] = (),
    question_set_id: str = "QSET-001",
    max_questions: int = 3,
    created_at: str | None = None,
) -> dict[str, Any]:
    """按稳定优先级取最多三个相关问题，禁止重复已处理主题。"""

    if not 1 <= max_questions <= 3:
        raise ValueError("max_questions 必须在 1～3 之间")
    history = {str(item) for item in history_question_keys}
    items = {str(item.get("key")): item for item in coverage.get("items", []) if isinstance(item, Mapping)}
    candidates = []
    for gap in gaps.get("gaps", []):
        if not isinstance(gap, Mapping) or not gap.get("question_allowed"):
            continue
        field_key = str(gap.get("field_key") or "")
        item = items.get(field_key, {})
        status = str(item.get("status") or "unanswered")
        question_key = str(item.get("last_question_key") or _QUESTION_KEYS.get(field_key, field_key))
        if status in _STATUS_DUPLICATE or question_key in history:
            continue
        if status not in _STATUS_REASK:
            continue
        candidates.append((gap, item, question_key))
    candidates.sort(key=lambda row: (
        _PRIORITY_RANK.get(str(row[0].get("priority")), 9),
        _IMPACT_RANK.get(str(row[1].get("decision_impact")), 9),
        _IMPACT_RANK.get(str(row[1].get("architecture_impact")), 9),
        _IMPACT_RANK.get(str(row[1].get("workflow_impact")), 9),
        0 if str(row[1].get("irreversibility")) == "high" else 1,
        -int(row[1].get("dependency_count") or 0),
        0 if str(row[1].get("risk")) == "high" else 1,
        0 if not bool(row[1].get("safe_assumption_available")) else 1,
        row[2],
    ))
    chosen = candidates[:max_questions]
    topic = _DIMENSION_TOPICS.get(chosen[0][1].get("key"), "product") if chosen else None
    if topic:
        same_topic = [row for row in candidates if _DIMENSION_TOPICS.get(row[1].get("key"), "product") == topic]
        chosen = same_topic[:max_questions] or chosen[:max_questions]
    questions = []
    for gap, item, question_key in chosen:
        questions.append({
            "question_key": question_key,
            "field_key": str(item.get("key")),
            "priority": str(gap.get("priority")),
            "reason": str(gap.get("reason")),
            "text": _question_text(str(item.get("key"))),
            "pre_question_status": str(item.get("status")),
            "source_refs": list(gap.get("source_refs") or []),
        })
    return {"schema_version": 1, "question_set_id": question_set_id, "project_id": project_id, "round": int(round_number), "gap_ref": str(gaps.get("gap_id") or ""), "topic": topic, "questions": questions, "created_at": created_at or _now(), "supersedes": None}


def evaluate_sufficiency(
    coverage: Mapping[str, Any],
    *,
    project_id: str,
    snapshot_material: object,
    evaluation_id: str = "SUFF-001",
    research_decision: str = "not_required",
    research_status: str = "not_required",
    risk_level: str = "low",
    created_at: str | None = None,
) -> dict[str, Any]:
    """确定性判断是否可以从 Intake 进入 Planner。"""

    required = {"product_intent", "target_users", "primary_goal", "primary_use_cases", "platform", "required_features", "data_model", "technical_constraints"}
    blocking: list[str] = []
    resolved: list[str] = []
    assumptions: list[str] = []
    design_routes: list[str] = []
    for item in coverage.get("items", []):
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "")
        status = str(item.get("status") or "unanswered")
        if key == "design_preferences" and status == "undecided":
            design_routes.append(key)
        if key in required:
            if status in {"unanswered", "conflicting", "requires_user_decision"}:
                blocking.append(key)
            elif status == "assumed":
                if not bool(item.get("safe_assumption_available")) or str(item.get("risk")) == "high":
                    blocking.append(key)
                else:
                    assumptions.append(key)
            else:
                resolved.append(key)
        elif status == "assumed":
            assumptions.append(key)
    if research_decision == "required" and risk_level == "regulated" and research_status in {"unavailable", "blocked"}:
        blocking.append("research:regulated_source_unavailable")
    if blocking:
        decision = "blocked" if any(item.startswith("research:") for item in blocking) else "waiting_user"
    else:
        decision = "sufficient_for_planning"
    return {
        "schema_version": 1,
        "evaluation_id": evaluation_id,
        "project_id": project_id,
        "decision": decision,
        "blocking_items": sorted(set(blocking)),
        "resolved_items": sorted(set(resolved)),
        "safe_assumptions": sorted(set(assumptions)),
        "routed_to_design_exploration": sorted(set(design_routes)),
        "input_snapshot_hash": _hash(snapshot_material),
        "rules_version": 1,
        "created_at": created_at or _now(),
        "supersedes": None,
    }
