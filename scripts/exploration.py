"""F4 产品与设计探索的确定性触发、工件校验和恢复判断。"""

from __future__ import annotations

import argparse
import copy
import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

try:
    from project_state import ProjectStateError, load_project_state
    from project_state import validate_project_state
except ModuleNotFoundError:  # 允许从仓库根目录以 scripts.exploration 导入
    from scripts.project_state import ProjectStateError, load_project_state
    from scripts.project_state import validate_project_state


REQUIRED_CONCEPTS = ("concept_01", "concept_02", "concept_03")
# 新协议按阶段区分工件，避免把第一阶段误当成三套高保真预览。
DIRECTION_REQUIRED_FILES = ("concept.md",)
SELECTED_PROTOTYPE_REQUIRED_FILES = ("concept.md", "preview.html", "preview.css")
LEGACY_REQUIRED_FILES = ("concept.md", "preview.html", "preview.css")
# 保留旧导入名，供历史调用方读取；新逻辑使用上面的阶段常量。
REQUIRED_FILES = SELECTED_PROTOTYPE_REQUIRED_FILES
DESIGN_PREVIEW_MODE_LEGACY = "legacy_full"
DESIGN_PREVIEW_MODE_COMPARISON = "direction_comparison"
DESIGN_PREVIEW_MODE_SELECTED = "selected_prototype"
DESIGN_PREVIEW_MODES = frozenset(
    {
        DESIGN_PREVIEW_MODE_LEGACY,
        DESIGN_PREVIEW_MODE_COMPARISON,
        DESIGN_PREVIEW_MODE_SELECTED,
    }
)
COMPARISON_FILES = ("comparison.html", "comparison.css")
SELECTED_CONCEPT_DIRECTORY = "selected_concept"
DIRECTION_REQUIRED_SECTIONS = (
    "方案名称",
    "一句话概念",
    "产品定位",
    "核心优势",
    "限制与取舍",
    "页面结构",
    "方案特色功能",
    "主要用户路径",
)
REQUIRED_SECTIONS = (
    "方案名称",
    "一句话概念",
    "产品定位",
    "目标用户",
    "主要使用场景",
    "设计理念",
    "核心优势",
    "限制与取舍",
    "UI 与视觉方向",
    "页面结构",
    "主要导航结构",
    "首页预览说明",
    "关键功能页预览说明",
    "基础功能",
    "方案特色功能",
    "MVP 功能",
    "后续扩展",
    "主要用户路径",
    "适用建议",
    "开发复杂度",
    "首版实施风险",
)
DISTINCTNESS_SECTIONS = ("产品定位", "核心优势", "方案特色功能", "主要用户路径")
MAXIMUM_GENERATION_ATTEMPTS = 2


DESIGN_RELEVANT_REFERENCE_DOMAINS = frozenset(
    {
        "information_architecture",
        "navigation",
        "interaction",
        "layout",
        "visual_style",
        "components",
        "design_tokens",
        "motion",
    }
)
REFERENCE_DECISION_ID_PATTERN = re.compile(r"\bREFDEC-\d{3}\b")
REFERENCE_SYNTHESIS_ID_PATTERN = re.compile(r"\bREFSYN-\d{3}\b")
REFERENCE_MODE_VALUES = frozenset({"inspiration", "adaptation", "close_recreation"})
REFERENCE_STRATEGY_VALUES = frozenset(
    {"reference_faithful", "reference_adapted", "reference_inspired"}
)
REFERENCE_GUIDED_SECTIONS = (
    "Reference Integration",
    "Reference Strategy",
    "Referenced Decisions",
    "Adopted Decisions",
    "Adapted Decisions",
    "Not Used Decisions",
    "Explicit Exclusions",
    "Original Design Decisions",
)
REFERENCE_DIMENSION_SECTIONS = (
    "Page Structure",
    "Navigation Structure",
    "Interaction Approach",
    "Visual Treatment",
    "Information Density",
    "主要页面结构",
    "主要导航结构",
    "交互方式",
    "视觉处理",
    "信息密度",
)
EXCLUSION_TOKENS = frozenset(
    {
        "brand",
        "brand_asset",
        "brand_assets",
        "logo",
        "marketing_copy",
        "marketing_content",
        "dark_theme",
        "dark_mode",
        "dark theme",
    }
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _all_synthesis_decisions(
    synthesis: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    decisions = _mapping(synthesis.get("decisions"))
    result: list[tuple[str, Mapping[str, Any]]] = []
    for bucket in ("adopt", "adapt", "avoid"):
        values = decisions.get(bucket, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping) and isinstance(item.get("decision_id"), str):
                result.append((bucket, item))
    return result


def _collect_exclusions(
    requirements: Mapping[str, Any] | None,
    source_metadata: Any,
    synthesis: Mapping[str, Any],
) -> set[str]:
    """集中读取硬排除项，避免 Planner 在不同概念中各自解释一次。"""

    exclusions: set[str] = set()
    requirement_map = _mapping(requirements)
    for key in ("explicit_exclusions", "excluded_domains", "exclusions"):
        values = requirement_map.get(key, [])
        if isinstance(values, list):
            exclusions.update(str(value).strip().lower() for value in values)
    references = requirement_map.get("references", [])
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, Mapping):
                values = reference.get("explicit_exclusions", [])
                if isinstance(values, list):
                    exclusions.update(str(value).strip().lower() for value in values)
    synthesis_context = _mapping(synthesis.get("context"))
    context_values = synthesis_context.get("explicit_exclusions", [])
    if isinstance(context_values, list):
        exclusions.update(str(value).strip().lower() for value in context_values)
    if isinstance(source_metadata, Mapping):
        source_metadata = [source_metadata]
    if isinstance(source_metadata, (list, tuple)):
        for source in source_metadata:
            source_map = _mapping(source)
            values = source_map.get("explicit_exclusions", [])
            if isinstance(values, list):
                exclusions.update(str(value).strip().lower() for value in values)
            scope = _mapping(source_map.get("requested_scope"))
            exclusions.update(
                str(key).strip().lower()
                for key, value in scope.items()
                if value == "exclude"
            )
    for bucket, decision in _all_synthesis_decisions(synthesis):
        if bucket == "avoid" or decision.get("user_scope_status") == "exclude":
            domain = decision.get("domain")
            if domain:
                exclusions.add(str(domain).strip().lower())
            if decision.get("decision_source") == "user_explicit_exclusion":
                pattern = decision.get("pattern")
                if pattern:
                    exclusions.add(str(pattern).strip().lower())
    return exclusions


def _decision_matches_exclusion(
    decision: Mapping[str, Any], exclusions: set[str]
) -> bool:
    domain = str(decision.get("domain", "")).strip().lower()
    if domain in exclusions:
        return True
    searchable = " ".join(
        str(decision.get(key, ""))
        for key in ("pattern", "rationale", "decision_source")
    ).lower()
    return any(token in searchable for token in EXCLUSION_TOKENS if token in exclusions)


def _assert_active_synthesis(
    synthesis: Mapping[str, Any], active_synthesis_id: str | None = None
) -> str:
    synthesis_id = synthesis.get("synthesis_id")
    if not isinstance(synthesis_id, str) or not REFERENCE_SYNTHESIS_ID_PATTERN.fullmatch(
        synthesis_id
    ):
        raise ProjectStateError("active_reference_synthesis 缺少有效 REFSYN 标识")
    if active_synthesis_id and synthesis_id != active_synthesis_id:
        raise ProjectStateError("设计探索引用了非当前 active_reference_synthesis")
    if synthesis.get("superseded") is True or synthesis.get("status") == "superseded":
        raise ProjectStateError("不得使用已 superseded 的 Reference Synthesis")
    return synthesis_id


def _resolve_reference_mode(
    reference_mode: str | None, source_metadata: Any
) -> str:
    if reference_mode is None:
        values: list[str] = []
        metadata = [source_metadata] if isinstance(source_metadata, Mapping) else source_metadata
        if isinstance(metadata, (list, tuple)):
            values = [
                str(_mapping(item).get("reference_mode"))
                for item in metadata
                if _mapping(item).get("reference_mode")
            ]
        if len(set(values)) > 1:
            raise ProjectStateError("多个 Reference mode 冲突，必须先由 Planner 解决")
        reference_mode = values[0] if values else "adaptation"
    if reference_mode not in REFERENCE_MODE_VALUES:
        raise ProjectStateError("reference_mode 无效")
    return reference_mode


def select_design_reference_decisions(
    synthesis: Mapping[str, Any],
    *,
    reference_mode: str | None = None,
    source_metadata: Any = None,
    requirements: Mapping[str, Any] | None = None,
    active_synthesis_id: str | None = None,
) -> dict[str, Any]:
    """从当前 synthesis 中选择设计相关 REFDEC；不读取 URL、原图或全量 Finding。"""

    synthesis_id = _assert_active_synthesis(synthesis, active_synthesis_id)
    conflicts = synthesis.get("conflicts", [])
    if isinstance(conflicts, list) and any(
        isinstance(item, Mapping)
        and item.get("resolution_status") == "requires_planner_resolution"
        and item.get("domain") in DESIGN_RELEVANT_REFERENCE_DOMAINS
        for item in conflicts
    ):
        raise ProjectStateError("设计相关 Reference conflict 尚未完成 Planner resolution")
    synthesis_mode = _mapping(synthesis.get("context")).get("reference_mode")
    mode = _resolve_reference_mode(
        reference_mode or (str(synthesis_mode) if synthesis_mode else None),
        source_metadata,
    )
    exclusions = _collect_exclusions(requirements, source_metadata, synthesis)
    selected: list[dict[str, Any]] = []
    excluded_ids: list[str] = []
    for bucket, decision in _all_synthesis_decisions(synthesis):
        decision_id = str(decision["decision_id"])
        domain = str(decision.get("domain", ""))
        if domain not in DESIGN_RELEVANT_REFERENCE_DOMAINS:
            continue
        if bucket == "avoid" or decision.get("user_scope_status") == "exclude":
            excluded_ids.append(decision_id)
            continue
        if _decision_matches_exclusion(decision, exclusions):
            excluded_ids.append(decision_id)
            continue
        selected.append({"bucket": bucket, **dict(decision)})
    selected.sort(key=lambda item: str(item["decision_id"]))
    return {
        "synthesis_id": synthesis_id,
        "reference_mode": mode,
        "source_references": [
            str(item) for item in synthesis.get("source_references", []) or []
        ],
        "decisions": selected,
        "decision_ids": [str(item["decision_id"]) for item in selected],
        "excluded_decision_ids": sorted(set(excluded_ids)),
        "explicit_exclusions": sorted(exclusions),
        "design_relevant": bool(selected),
    }


def assign_reference_strategies(
    reference_mode: str,
    *,
    design_decisions: Any = None,
    explicit_exclusions: Any = None,
) -> list[dict[str, Any]]:
    """按 Reference mode 固定三条策略，禁止模型把三条路线生成成同一种。"""

    if reference_mode not in REFERENCE_MODE_VALUES:
        raise ProjectStateError("reference_mode 无效")
    if not design_decisions:
        return []
    exclusions = {str(item).lower() for item in (explicit_exclusions or [])}
    close_allowed = reference_mode == "close_recreation" and "close_recreation" not in exclusions
    if reference_mode == "inspiration":
        plan = (
            ("reference_inspired", "strong_inspiration"),
            ("reference_adapted", "balanced_inspiration"),
            ("reference_inspired", "original_interpretation"),
        )
    elif reference_mode == "close_recreation" and close_allowed:
        plan = (
            ("reference_faithful", "close_recreation"),
            ("reference_adapted", "balanced_adaptation"),
            ("reference_inspired", "original_interpretation"),
        )
    else:
        plan = (
            ("reference_faithful", "faithful_adaptation"),
            ("reference_adapted", "balanced_adaptation"),
            ("reference_inspired", "original_interpretation"),
        )
    return [
        {
            "concept_id": f"concept_{index:02d}",
            "reference_strategy": strategy,
            "strategy": variant,
            "strategy_variant": variant,
            "reference_mode": reference_mode,
        }
        for index, (strategy, variant) in enumerate(plan, 1)
    ]


def build_reference_integration_metadata(
    synthesis: Mapping[str, Any],
    *,
    reference_mode: str | None = None,
    source_metadata: Any = None,
    requirements: Mapping[str, Any] | None = None,
    active_synthesis_id: str | None = None,
) -> list[dict[str, Any]]:
    """生成三份概念工件应声明的 Reference Integration 元数据。"""

    selected = select_design_reference_decisions(
        synthesis,
        reference_mode=reference_mode,
        source_metadata=source_metadata,
        requirements=requirements,
        active_synthesis_id=active_synthesis_id,
    )
    decisions = list(selected["decisions"])
    strategies = assign_reference_strategies(
        selected["reference_mode"],
        design_decisions=decisions,
        explicit_exclusions=selected["explicit_exclusions"],
    )
    all_ids = [str(item["decision_id"]) for item in decisions]
    adopted = [str(item["decision_id"]) for item in decisions if item["bucket"] == "adopt"]
    adapted = [str(item["decision_id"]) for item in decisions if item["bucket"] == "adapt"]
    result: list[dict[str, Any]] = []
    for index, strategy in enumerate(strategies):
        referenced = list(all_ids) if index < 2 else (
            all_ids[: max(1, (len(all_ids) + 1) // 2)] if all_ids else []
        )
        used = set(referenced)
        result.append(
            {
                **strategy,
                "reference_synthesis": selected["synthesis_id"],
                "referenced_decisions": referenced,
                "adopted_decisions": [item for item in adopted if item in used],
                "adapted_decisions": [item for item in adapted if item in used],
                "not_used_decisions": [item for item in all_ids if item not in used],
                "explicit_exclusions": list(selected["explicit_exclusions"]),
                "original_design_decisions": [
                    "满足 active requirements，不能牺牲显式用户要求",
                    f"采用 {strategy['strategy_variant']} 的独立产品表达",
                ],
            }
        )
    return result


def render_reference_integration_metadata(metadata: Mapping[str, Any]) -> str:
    """把确定性 Reference 元数据渲染为 concept.md 的追加式章节。"""

    def _items(values: Any) -> list[str]:
        return [f"- {item}" for item in values] if values else ["- None"]

    lines = [
        "## Reference Integration",
        f"- Reference Synthesis: {metadata.get('reference_synthesis', 'None')}",
        f"- Reference Mode: {metadata.get('reference_mode', 'None')}",
        f"- Reference Strategy: {metadata.get('reference_strategy', 'None')}",
        f"- Strategy: {metadata.get('strategy', metadata.get('strategy_variant', 'None'))}",
        f"- Strategy Variant: {metadata.get('strategy_variant', 'None')}",
        "",
        "## Reference Strategy",
        f"{metadata.get('reference_strategy', 'None')} / {metadata.get('strategy_variant', 'None')}",
        "",
        "## Referenced Decisions",
        *_items(metadata.get("referenced_decisions")),
        "",
        "## Adopted Decisions",
        *_items(metadata.get("adopted_decisions")),
        "",
        "## Adapted Decisions",
        *_items(metadata.get("adapted_decisions")),
        "",
        "## Not Used Decisions",
        *_items(metadata.get("not_used_decisions")),
        "",
        "## Explicit Exclusions",
        *_items(metadata.get("explicit_exclusions")),
        "",
        "## Original Design Decisions",
        *_items(metadata.get("original_design_decisions")),
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ExplorationDecision:
    action: str
    required: bool | None
    reasons: tuple[str, ...]


def decide_design_exploration(
    requirements: dict[str, Any],
    *,
    user_requested: bool = False,
    user_explicitly_approved_skip: bool = False,
) -> ExplorationDecision:
    """根据结构化需求判断探索、跳过确认或等待确认。

    `required=None` 表示设计规范完整，但仍需用户明确确认是否跳过。
    """

    if user_requested and user_explicitly_approved_skip:
        raise ProjectStateError("用户请求探索与明确跳过不能同时成立")

    preferences = requirements.get("design_preferences") or {}
    if not isinstance(preferences, dict):
        raise ProjectStateError("design_preferences 必须是映射")
    status = preferences.get("status", "unanswered")
    routing = preferences.get("routing")
    completeness = preferences.get("specification_completeness", "none")
    if completeness not in {"none", "partial", "complete"}:
        raise ProjectStateError("design_preferences.specification_completeness 无效")

    reasons: list[str] = []
    if user_requested:
        reasons.append("user_requested_previews")
    if status == "undecided":
        reasons.append("visual_preferences_undecided")
    if routing == "design_exploration":
        reasons.append("requirements_routed_to_design_exploration")
    if completeness != "complete":
        reasons.append("design_specification_incomplete")

    if user_requested:
        return ExplorationDecision("exploration_required", True, tuple(reasons))
    if user_explicitly_approved_skip:
        if completeness == "complete":
            return ExplorationDecision(
                "skip_allowed", False, ("complete_specification", "explicit_skip_approval")
            )
        reasons.append("skip_rejected_incomplete_specification")
        return ExplorationDecision("exploration_required", True, tuple(dict.fromkeys(reasons)))
    if reasons:
        return ExplorationDecision("exploration_required", True, tuple(dict.fromkeys(reasons)))
    return ExplorationDecision(
        "await_skip_confirmation", None, ("complete_specification",)
    )


def begin_exploration(
    state: dict[str, Any],
    decision: ExplorationDecision,
    *,
    active_proposal: str,
    round_reference: str,
    reference_synthesis_id: str | None = None,
    preview_mode: str = DESIGN_PREVIEW_MODE_COMPARISON,
) -> dict[str, Any]:
    """创建进入 DESIGN_EXPLORATION 的候选状态，不直接写文件。"""

    if decision.action != "exploration_required" or decision.required is not True:
        raise ProjectStateError("只有 exploration_required 决策可以开始探索")
    if state.get("requirements_status") != "sufficient_for_planning":
        raise ProjectStateError("需求未达到 sufficient_for_planning")
    if not state.get("active_requirements"):
        raise ProjectStateError("开始探索前 active_requirements 不能为空")
    if preview_mode not in DESIGN_PREVIEW_MODES:
        raise ProjectStateError("design_preview_mode 无效")
    if preview_mode == DESIGN_PREVIEW_MODE_SELECTED and (
        not state.get("selected_design_concept")
        or not state.get("design_selection_record")
    ):
        raise ProjectStateError("生成单一高保真预览前必须保留方向选择来源")
    attempt = state.get("exploration_generation_attempt", 0) + 1
    if attempt > MAXIMUM_GENERATION_ATTEMPTS:
        raise ProjectStateError("产品探索自动生成已达到两次上限")

    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "DESIGN_EXPLORATION",
            "next_role": "planner",
            "active_module": None,
            "active_proposal": active_proposal,
            "approved_proposal": None,
            "design_exploration_required": True,
            "exploration_trigger_reasons": list(decision.reasons),
            "design_review_status": "generating",
            "design_preview_mode": preview_mode,
            "active_design_preview_round": round_reference,
            "exploration_generation_attempt": attempt,
            "active_plan": None,
            "active_design_reference_synthesis": reference_synthesis_id,
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("无法进入设计探索：" + "; ".join(errors))
    return updated


def build_design_exploration_context(
    active_requirements: Any,
    active_product_proposal: Any,
    *,
    active_reference_synthesis: Mapping[str, Any] | None = None,
    reference_mode: str | None = None,
    source_metadata: Any = None,
    previous_feedback: Any = None,
) -> dict[str, Any]:
    """构建 Planner Design Exploration 的最小上下文，不带入原始 URL 或媒体正文。"""

    context: dict[str, Any] = {
        "active_requirements": active_requirements,
        "active_product_proposal": active_product_proposal,
        "previous_feedback": previous_feedback,
        "active_reference_synthesis": None,
    }
    if active_reference_synthesis is None:
        return context
    selected = select_design_reference_decisions(
        active_reference_synthesis,
        reference_mode=reference_mode,
        source_metadata=source_metadata,
        requirements=active_requirements if isinstance(active_requirements, Mapping) else None,
    )
    context["active_reference_synthesis"] = {
        "synthesis_id": selected["synthesis_id"],
        "source_references": selected["source_references"],
        "reference_mode": selected["reference_mode"],
        "design_relevant_decisions": selected["decisions"],
        "explicit_exclusions": selected["explicit_exclusions"],
        "excluded_decision_ids": selected["excluded_decision_ids"],
    }
    context["reference_strategy_plan"] = assign_reference_strategies(
        selected["reference_mode"],
        design_decisions=selected["decisions"],
        explicit_exclusions=selected["explicit_exclusions"],
    )
    return context


def _load_synthesis_pointer(
    project_root: str | Path, pointer: str
) -> tuple[str, dict[str, Any]]:
    root = Path(project_root).resolve()
    path = _resolve_inside(root, pointer)
    if not path.is_file():
        raise ProjectStateError("active_reference_synthesis 指向的工件不存在")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectStateError("active_reference_synthesis 无法读取") from exc
    if not isinstance(value, dict):
        raise ProjectStateError("active_reference_synthesis 不是对象")
    synthesis_id = _assert_active_synthesis(value)
    return synthesis_id, value


def finalize_preview_round(
    state: dict[str, Any], project_root: str | Path
) -> dict[str, Any]:
    """只有预览完整有效时才进入 WAITING_FOR_DESIGN_REVIEW。"""

    if state.get("status") != "DESIGN_EXPLORATION":
        raise ProjectStateError("只有 DESIGN_EXPLORATION 可以完成预览轮次")
    reference = state.get("active_design_preview_round")
    if not reference:
        raise ProjectStateError("active_design_preview_round 不能为空")
    synthesis = None
    active_synthesis_id = state.get("active_design_reference_synthesis")
    active_pointer = state.get("active_reference_synthesis")
    if active_synthesis_id and not active_pointer:
        raise ProjectStateError("设计探索绑定了 Reference Synthesis，但项目没有 active 指针")
    if active_pointer:
        active_synthesis_id_from_file, synthesis = _load_synthesis_pointer(
            project_root, str(active_pointer)
        )
        if active_synthesis_id and active_synthesis_id != active_synthesis_id_from_file:
            raise ProjectStateError("当前设计轮次绑定的 Reference Synthesis 已发生变化")
        active_synthesis_id = active_synthesis_id_from_file
    preview_errors = validate_preview_round(
        project_root,
        reference,
        preview_mode=str(state.get("design_preview_mode") or DESIGN_PREVIEW_MODE_LEGACY),
        reference_synthesis=synthesis,
        active_synthesis_id=active_synthesis_id,
    )
    if preview_errors:
        raise ProjectStateError("预览轮次校验失败：" + "; ".join(preview_errors))
    updated = copy.deepcopy(state)
    preview_mode = str(state.get("design_preview_mode") or DESIGN_PREVIEW_MODE_LEGACY)
    updated.update(
        {
            "status": "WAITING_FOR_DESIGN_REVIEW",
            "next_role": "planner",
            "design_review_status": (
                "waiting_selected_prototype_confirmation"
                if preview_mode == DESIGN_PREVIEW_MODE_SELECTED
                else "waiting_user_selection"
            ),
            "design_feedback_status": "waiting_user_feedback",
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("无法进入设计审核等待：" + "; ".join(errors))
    return updated


def apply_explicit_skip(
    state: dict[str, Any],
    decision: ExplorationDecision,
    *,
    design_skip_record: str,
) -> dict[str, Any]:
    """应用用户明确同意的跳过决定，不把跳过解释为产品批准。"""

    if decision.action != "skip_allowed" or decision.required is not False:
        raise ProjectStateError("只有 skip_allowed 决策可以跳过探索")
    if not design_skip_record:
        raise ProjectStateError("跳过探索必须有追加式 design_skip_record")
    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "WAITING_FOR_PRODUCT_REVIEW",
            "next_role": "planner",
            "design_exploration_required": False,
            "exploration_trigger_reasons": list(decision.reasons),
            "design_review_status": "skipped_by_user",
            "design_skip_record": design_skip_record,
            "active_design_preview_round": None,
            "selected_design_concept": None,
            "design_selection_record": None,
            "active_plan": None,
            "approved_proposal": None,
            "user_approval_status": "waiting_explicit_confirmation",
            "proposal_status": "waiting_user_review",
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("无法应用跳过决定：" + "; ".join(errors))
    return updated


def _extract_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _section_value(sections: Mapping[str, str], *names: str) -> str:
    for name in names:
        if sections.get(name):
            return str(sections[name])
    lowered = {str(key).strip().lower(): value for key, value in sections.items()}
    for name in names:
        if lowered.get(name.strip().lower()):
            return str(lowered[name.strip().lower()])
    return ""


def _section_decision_ids(sections: Mapping[str, str], *names: str) -> set[str]:
    return set(REFERENCE_DECISION_ID_PATTERN.findall(_section_value(sections, *names)))


def _section_synthesis_id(sections: Mapping[str, str]) -> str | None:
    value = _section_value(sections, "Reference Integration", "Reference Strategy")
    match = REFERENCE_SYNTHESIS_ID_PATTERN.search(value)
    return match.group(0) if match else None


def _section_strategy(sections: Mapping[str, str]) -> str | None:
    value = _section_value(sections, "Reference Integration", "Reference Strategy")
    for strategy in REFERENCE_STRATEGY_VALUES:
        if strategy in value:
            return strategy
    return None


def _section_strategy_variant(sections: Mapping[str, str]) -> str | None:
    value = _section_value(sections, "Reference Integration", "Reference Strategy")
    for variant in (
        "strong_inspiration",
        "balanced_inspiration",
        "faithful_adaptation",
        "balanced_adaptation",
        "close_recreation",
        "original_interpretation",
    ):
        if variant in value:
            return variant
    return None


def validate_concept_reference_coverage(
    concept_text: str,
    synthesis: Mapping[str, Any],
    *,
    expected_synthesis_id: str | None = None,
    requirements: Mapping[str, Any] | None = None,
    source_metadata: Any = None,
    expected_strategy: Mapping[str, Any] | None = None,
) -> list[str]:
    """校验 concept.md 的 REFDEC 链、策略、排除项和当前 synthesis 绑定。"""

    errors: list[str] = []
    try:
        selected = select_design_reference_decisions(
            synthesis,
            source_metadata=source_metadata,
            requirements=requirements,
            active_synthesis_id=expected_synthesis_id,
        )
    except ProjectStateError as exc:
        return [str(exc)]
    sections = _extract_sections(concept_text)
    missing = [section for section in REFERENCE_GUIDED_SECTIONS if not sections.get(section)]
    if missing:
        errors.append(f"Reference-guided concept 缺少章节: {', '.join(missing)}")
        return errors
    synthesis_id = _section_synthesis_id(sections)
    if synthesis_id != selected["synthesis_id"]:
        errors.append("concept.md 未绑定当前 active Reference Synthesis")
    strategy = _section_strategy(sections)
    if not strategy:
        errors.append("concept.md 缺少有效 Reference Strategy")
    if expected_strategy:
        expected = expected_strategy.get("reference_strategy")
        if expected and strategy != expected:
            errors.append("concept.md 的 Reference Strategy 与 Planner 分配结果不一致")
        expected_variant = expected_strategy.get("strategy_variant")
        if expected_variant and _section_strategy_variant(sections) != expected_variant:
            errors.append("concept.md 的策略变体与 Planner 分配结果不一致")
    if selected["reference_mode"] == "inspiration" and strategy == "reference_faithful":
        errors.append("inspiration 模式不得生成 reference_faithful 路线")

    known_ids = set(selected["decision_ids"])
    all_ids = set(REFERENCE_DECISION_ID_PATTERN.findall(concept_text))
    if not all_ids.issubset(known_ids):
        errors.append("concept.md 引用了 active synthesis 之外或非设计相关的 REFDEC")
    adopted = _section_decision_ids(sections, "Adopted Decisions")
    adapted = _section_decision_ids(sections, "Adapted Decisions")
    referenced = _section_decision_ids(sections, "Referenced Decisions")
    not_used = _section_decision_ids(sections, "Not Used Decisions")
    if not adopted.issubset(referenced) or not adapted.issubset(referenced):
        errors.append("Adopted/Adapted Decisions 必须是 Referenced Decisions 的子集")
    if not_used & (adopted | adapted):
        errors.append("同一 REFDEC 不能同时属于已采用和 Not Used")
    if selected["explicit_exclusions"]:
        excluded_ids = set(selected["excluded_decision_ids"])
        if all_ids & excluded_ids or adopted & excluded_ids or adapted & excluded_ids:
            errors.append("concept.md 采用了显式排除的 Reference Decision")
        exclusion_text = _section_value(sections, "Explicit Exclusions").lower()
        for exclusion in selected["explicit_exclusions"]:
            if exclusion not in exclusion_text:
                errors.append(f"concept.md 未声明硬排除项: {exclusion}")
        protected_text = " ".join(
            _section_value(
                sections,
                "UI 与视觉方向",
                "UI and Visual Direction",
                "Original Design Decisions",
                "Adopted Decisions",
                "Adapted Decisions",
            ).lower().split()
        )
        for exclusion in selected["explicit_exclusions"]:
            if exclusion in EXCLUSION_TOKENS and exclusion in protected_text:
                errors.append(f"concept.md 在设计表达中违反硬排除项: {exclusion}")
    requirement_map = _mapping(requirements)
    required_values: list[str] = []
    for key in ("explicit_requirements", "must_have", "required_features"):
        values = requirement_map.get(key, [])
        if isinstance(values, list):
            required_values.extend(str(value).strip().lower() for value in values if value)
    concept_lower = concept_text.lower()
    for requirement in required_values:
        if requirement and requirement not in concept_lower:
            errors.append(f"concept.md 未体现显式需求: {requirement}")
    return errors


def _validate_concept_dimensions(
    concept_sections: Mapping[str, Mapping[str, str]],
    *,
    reference_guided: bool,
) -> list[str]:
    errors: list[str] = []
    for left, right in itertools.combinations(REQUIRED_CONCEPTS, 2):
        if left not in concept_sections or right not in concept_sections:
            continue
        legacy_differences = sum(
            _normalize_difference_text(concept_sections[left].get(section, ""))
            != _normalize_difference_text(concept_sections[right].get(section, ""))
            for section in DISTINCTNESS_SECTIONS
        )
        if legacy_differences < 2:
            errors.append(
                f"{left} 与 {right} 的产品路线差异不足：产品定位、核心优势、特色功能、主要用户路径至少两项不同"
            )
            continue
        if reference_guided:
            structural_sections = [
                section
                for section in REFERENCE_DIMENSION_SECTIONS
                if section in concept_sections[left] or section in concept_sections[right]
            ]
            structural_differences = sum(
                _normalize_difference_text(concept_sections[left].get(section, ""))
                != _normalize_difference_text(concept_sections[right].get(section, ""))
                for section in structural_sections
            )
            if structural_sections and structural_differences == 0:
                errors.append(f"{left} 与 {right} 不能只更换 Reference 策略标签")
    return errors


def validate_reference_guided_concepts(
    project_root: str | Path,
    round_reference: str,
    synthesis: Mapping[str, Any],
    *,
    reference_mode: str | None = None,
    source_metadata: Any = None,
    requirements: Mapping[str, Any] | None = None,
    active_synthesis_id: str | None = None,
) -> list[str]:
    """对现有预览目录执行 Reference-guided 专用校验。"""

    return validate_preview_round(
        project_root,
        round_reference,
        reference_synthesis=synthesis,
        reference_mode=reference_mode,
        source_metadata=source_metadata,
        requirements=requirements,
        active_synthesis_id=active_synthesis_id,
    )


def _normalize_difference_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _resolve_inside(root: Path, reference: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / reference).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ProjectStateError("设计预览路径指向项目目录之外") from exc
    return candidate


def validate_preview_round(
    project_root: str | Path,
    round_reference: str,
    *,
    reference_synthesis: Mapping[str, Any] | None = None,
    active_reference_synthesis: Mapping[str, Any] | None = None,
    reference_mode: str | None = None,
    source_metadata: Any = None,
    requirements: Mapping[str, Any] | None = None,
    active_synthesis_id: str | None = None,
    preview_mode: str = DESIGN_PREVIEW_MODE_COMPARISON,
) -> list[str]:
    if preview_mode not in DESIGN_PREVIEW_MODES:
        return [f"未知 design_preview_mode：{preview_mode}"]
    if preview_mode == DESIGN_PREVIEW_MODE_COMPARISON:
        return _validate_direction_comparison_round(
            project_root,
            round_reference,
            reference_synthesis=reference_synthesis or active_reference_synthesis,
            reference_mode=reference_mode,
            source_metadata=source_metadata,
            requirements=requirements,
            active_synthesis_id=active_synthesis_id,
        )
    if preview_mode == DESIGN_PREVIEW_MODE_SELECTED:
        return _validate_selected_prototype_round(
            project_root,
            round_reference,
            reference_synthesis=reference_synthesis or active_reference_synthesis,
            source_metadata=source_metadata,
            requirements=requirements,
            active_synthesis_id=active_synthesis_id,
        )
    root = Path(project_root)
    try:
        round_dir = _resolve_inside(root, round_reference)
    except ProjectStateError as exc:
        return [str(exc)]
    if not round_dir.is_dir():
        return [f"缺少设计预览轮次目录：{round_reference}"]

    errors: list[str] = []
    if reference_synthesis is None:
        reference_synthesis = active_reference_synthesis
    selected_reference: dict[str, Any] | None = None
    reference_guided = reference_synthesis is not None
    expected_strategies: dict[str, Mapping[str, Any]] = {}
    if reference_synthesis is not None:
        try:
            selected_reference = select_design_reference_decisions(
                reference_synthesis,
                reference_mode=reference_mode,
                source_metadata=source_metadata,
                requirements=requirements,
                active_synthesis_id=active_synthesis_id,
            )
        except ProjectStateError as exc:
            return [str(exc)]
        # 技术性或未提供语义决策的 synthesis 不应强行进入视觉参考模式。
        reference_guided = bool(selected_reference["design_relevant"])
        if reference_guided:
            expected_strategies = {
                item["concept_id"]: item
                for item in assign_reference_strategies(
                    selected_reference["reference_mode"],
                    design_decisions=selected_reference["decisions"],
                    explicit_exclusions=selected_reference["explicit_exclusions"],
                )
            }
    present_concepts = sorted(
        item.name
        for item in round_dir.iterdir()
        if item.is_dir() and re.fullmatch(r"concept_\d+", item.name)
    )
    if present_concepts != list(REQUIRED_CONCEPTS):
        missing = sorted(set(REQUIRED_CONCEPTS) - set(present_concepts))
        extras = sorted(set(present_concepts) - set(REQUIRED_CONCEPTS))
        if missing and not extras:
            errors.append(f"缺少概念目录：{', '.join(missing)}")
        else:
            errors.append(
                "设计预览必须恰好包含 concept_01、concept_02、concept_03；"
                f"当前为 {present_concepts}"
            )

    concept_sections: dict[str, dict[str, str]] = {}
    for concept_name in REQUIRED_CONCEPTS:
        concept_dir = round_dir / concept_name
        if not concept_dir.is_dir():
            errors.append(f"缺少概念目录：{concept_name}")
            continue
        for file_name in LEGACY_REQUIRED_FILES:
            file_path = concept_dir / file_name
            if not file_path.is_file():
                errors.append(f"缺少文件：{concept_name}/{file_name}")
            elif not file_path.read_text(encoding="utf-8").strip():
                errors.append(f"文件为空：{concept_name}/{file_name}")

        concept_path = concept_dir / "concept.md"
        if concept_path.is_file():
            concept_text = concept_path.read_text(encoding="utf-8")
            sections = _extract_sections(concept_text)
            concept_sections[concept_name] = sections
            if (
                not reference_guided
                and (
                    REFERENCE_DECISION_ID_PATTERN.search(concept_text)
                    or _section_strategy(sections)
                    or _section_synthesis_id(sections)
                )
            ):
                errors.append(f"{concept_name}/concept.md 存在未绑定的 Reference Integration")
            for section in REQUIRED_SECTIONS:
                if not sections.get(section):
                    errors.append(f"{concept_name}/concept.md 缺少有效章节：{section}")

        html_path = concept_dir / "preview.html"
        if html_path.is_file():
            html = html_path.read_text(encoding="utf-8")
            for marker in (
                'href="preview.css"',
                'data-preview-page="home"',
                'data-preview-page="key-feature"',
                "data-preview-nav",
            ):
                if marker not in html:
                    errors.append(f"{concept_name}/preview.html 缺少标记：{marker}")
            if reference_guided and selected_reference is not None:
                expected = expected_strategies.get(concept_name, {})
                html_markers = (
                    f'data-concept-id="{concept_name}"',
                    f'data-reference-synthesis="{selected_reference["synthesis_id"]}"',
                    f'data-reference-strategy="{expected.get("reference_strategy", "")}"',
                )
                for marker in html_markers:
                    if marker not in html:
                        errors.append(f"{concept_name}/preview.html 缺少 Reference 标记：{marker}")
        css_path = concept_dir / "preview.css"
        if reference_guided and css_path.is_file():
            css = css_path.read_text(encoding="utf-8")
            if "reference-strategy" not in css and "reference_strategy" not in css:
                errors.append(f"{concept_name}/preview.css 未声明 Reference strategy 的视觉实现钩子")

    errors.extend(
        _validate_concept_dimensions(
            concept_sections,
            reference_guided=reference_guided,
        )
    )
    if reference_guided and selected_reference is not None:
        for concept_name in REQUIRED_CONCEPTS:
            concept_path = round_dir / concept_name / "concept.md"
            if not concept_path.is_file():
                continue
            concept_text = concept_path.read_text(encoding="utf-8")
            errors.extend(
                f"{concept_name}: {error}"
                for error in validate_concept_reference_coverage(
                    concept_text,
                    reference_synthesis,
                    expected_synthesis_id=selected_reference["synthesis_id"],
                    requirements=requirements,
                    source_metadata=source_metadata,
                    expected_strategy=expected_strategies.get(concept_name),
                )
            )
    return errors


def _prepare_reference_validation(
    reference_synthesis: Mapping[str, Any] | None,
    *,
    reference_mode: str | None,
    source_metadata: Any,
    requirements: Mapping[str, Any] | None,
    active_synthesis_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Mapping[str, Any]], list[str]]:
    if reference_synthesis is None:
        return None, {}, []
    try:
        selected = select_design_reference_decisions(
            reference_synthesis,
            reference_mode=reference_mode,
            source_metadata=source_metadata,
            requirements=requirements,
            active_synthesis_id=active_synthesis_id,
        )
    except ProjectStateError as exc:
        return None, {}, [str(exc)]
    if not selected["design_relevant"]:
        return None, {}, []
    strategies = {
        item["concept_id"]: item
        for item in assign_reference_strategies(
            selected["reference_mode"],
            design_decisions=selected["decisions"],
            explicit_exclusions=selected["explicit_exclusions"],
        )
    }
    return selected, strategies, []


def _validate_direction_comparison_round(
    project_root: str | Path,
    round_reference: str,
    *,
    reference_synthesis: Mapping[str, Any] | None,
    reference_mode: str | None,
    source_metadata: Any,
    requirements: Mapping[str, Any] | None,
    active_synthesis_id: str | None,
) -> list[str]:
    """校验轻量方向比较：三份路线说明，共用一个比较页面。"""

    root = Path(project_root)
    try:
        round_dir = _resolve_inside(root, round_reference)
    except ProjectStateError as exc:
        return [str(exc)]
    if not round_dir.is_dir():
        return [f"缺少设计预览轮次目录：{round_reference}"]

    selected_reference, strategies, reference_errors = _prepare_reference_validation(
        reference_synthesis,
        reference_mode=reference_mode,
        source_metadata=source_metadata,
        requirements=requirements,
        active_synthesis_id=active_synthesis_id,
    )
    if reference_errors:
        return reference_errors
    reference_guided = selected_reference is not None
    errors: list[str] = []
    present_concepts = sorted(
        item.name
        for item in round_dir.iterdir()
        if item.is_dir() and re.fullmatch(r"concept_\d+", item.name)
    )
    if present_concepts != list(REQUIRED_CONCEPTS):
        errors.append(
            "方向比较必须恰好包含 concept_01、concept_02、concept_03；"
            f"当前为 {present_concepts}"
        )

    concept_sections: dict[str, dict[str, str]] = {}
    for concept_name in REQUIRED_CONCEPTS:
        concept_path = round_dir / concept_name / "concept.md"
        if not concept_path.is_file():
            errors.append(f"缺少文件：{concept_name}/concept.md")
            continue
        for forbidden_file in ("preview.html", "preview.css"):
            if (round_dir / concept_name / forbidden_file).exists():
                errors.append(
                    f"方向比较阶段禁止生成：{concept_name}/{forbidden_file}；"
                    "高保真预览只能位于 selected_concept"
                )
        concept_text = concept_path.read_text(encoding="utf-8")
        if not concept_text.strip():
            errors.append(f"文件为空：{concept_name}/concept.md")
            continue
        sections = _extract_sections(concept_text)
        concept_sections[concept_name] = sections
        for section in DIRECTION_REQUIRED_SECTIONS:
            if not sections.get(section):
                errors.append(f"{concept_name}/concept.md 缺少有效章节：{section}")
        if reference_guided and reference_synthesis is not None and selected_reference is not None:
            errors.extend(
                f"{concept_name}: {error}"
                for error in validate_concept_reference_coverage(
                    concept_text,
                    reference_synthesis,
                    expected_synthesis_id=selected_reference["synthesis_id"],
                    requirements=requirements,
                    source_metadata=source_metadata,
                    expected_strategy=strategies.get(concept_name),
                )
            )
        elif (
            REFERENCE_DECISION_ID_PATTERN.search(concept_text)
            or _section_strategy(sections)
            or _section_synthesis_id(sections)
        ):
            errors.append(f"{concept_name}/concept.md 存在未绑定的 Reference Integration")

    for file_name in COMPARISON_FILES:
        path = round_dir / file_name
        if not path.is_file():
            errors.append(f"缺少文件：{file_name}")
        elif not path.read_text(encoding="utf-8").strip():
            errors.append(f"文件为空：{file_name}")
    comparison_html = round_dir / "comparison.html"
    if comparison_html.is_file():
        html = comparison_html.read_text(encoding="utf-8")
        for marker in (
            'href="comparison.css"',
            'data-preview-mode="direction-comparison"',
            'data-concept-card="concept_01"',
            'data-concept-card="concept_02"',
            'data-concept-card="concept_03"',
        ):
            if marker not in html:
                errors.append(f"comparison.html 缺少标记：{marker}")

    errors.extend(
        _validate_concept_dimensions(concept_sections, reference_guided=reference_guided)
    )
    return errors


def _validate_selected_prototype_round(
    project_root: str | Path,
    round_reference: str,
    *,
    reference_synthesis: Mapping[str, Any] | None,
    source_metadata: Any,
    requirements: Mapping[str, Any] | None,
    active_synthesis_id: str | None,
) -> list[str]:
    """校验用户选定路线后的唯一高保真预览。"""

    root = Path(project_root)
    try:
        round_dir = _resolve_inside(root, round_reference)
    except ProjectStateError as exc:
        return [str(exc)]
    if not round_dir.is_dir():
        return [f"缺少设计预览轮次目录：{round_reference}"]

    errors: list[str] = []
    extra_concepts = sorted(
        item.name
        for item in round_dir.iterdir()
        if item.is_dir() and re.fullmatch(r"concept_\d+", item.name)
    )
    if extra_concepts:
        errors.append(
            "selected_prototype 轮次只能包含 selected_concept，禁止残留方向目录："
            + ", ".join(extra_concepts)
        )
    for comparison_file in COMPARISON_FILES:
        if (round_dir / comparison_file).exists():
            errors.append(
                f"selected_prototype 轮次禁止包含方向比较工件：{comparison_file}"
            )
    concept_dir = round_dir / SELECTED_CONCEPT_DIRECTORY
    if not concept_dir.is_dir():
        return [f"缺少概念目录：{SELECTED_CONCEPT_DIRECTORY}"]
    for file_name in SELECTED_PROTOTYPE_REQUIRED_FILES:
        path = concept_dir / file_name
        if not path.is_file():
            errors.append(f"缺少文件：{SELECTED_CONCEPT_DIRECTORY}/{file_name}")
        elif not path.read_text(encoding="utf-8").strip():
            errors.append(f"文件为空：{SELECTED_CONCEPT_DIRECTORY}/{file_name}")

    concept_path = concept_dir / "concept.md"
    if concept_path.is_file():
        concept_text = concept_path.read_text(encoding="utf-8")
        sections = _extract_sections(concept_text)
        for section in REQUIRED_SECTIONS:
            if not sections.get(section):
                errors.append(
                    f"{SELECTED_CONCEPT_DIRECTORY}/concept.md 缺少有效章节：{section}"
                )
        if reference_synthesis is not None:
            selected_reference, _, reference_errors = _prepare_reference_validation(
                reference_synthesis,
                reference_mode=None,
                source_metadata=source_metadata,
                requirements=requirements,
                active_synthesis_id=active_synthesis_id,
            )
            errors.extend(reference_errors)
            if selected_reference is not None:
                errors.extend(
                    f"{SELECTED_CONCEPT_DIRECTORY}: {error}"
                    for error in validate_concept_reference_coverage(
                        concept_text,
                        reference_synthesis,
                        expected_synthesis_id=selected_reference["synthesis_id"],
                        requirements=requirements,
                        source_metadata=source_metadata,
                    )
                )

    html_path = concept_dir / "preview.html"
    if html_path.is_file():
        html = html_path.read_text(encoding="utf-8")
        for marker in (
            'href="preview.css"',
            'data-preview-mode="selected-prototype"',
            'data-preview-page="home"',
            'data-preview-page="key-feature"',
            "data-preview-nav",
        ):
            if marker not in html:
                errors.append(
                    f"{SELECTED_CONCEPT_DIRECTORY}/preview.html 缺少标记：{marker}"
                )
    return errors


def assess_exploration_recovery(
    state: dict[str, Any], project_root: str | Path
) -> str:
    """判断中断后应恢复等待、补齐本轮、开启新轮或人工处理。"""

    status = state.get("status")
    if status not in {"DESIGN_EXPLORATION", "WAITING_FOR_DESIGN_REVIEW"}:
        return "not_in_exploration"
    round_reference = state.get("active_design_preview_round")
    if not round_reference:
        return "start_new_round"
    errors = validate_preview_round(
        project_root,
        round_reference,
        preview_mode=str(state.get("design_preview_mode") or DESIGN_PREVIEW_MODE_LEGACY),
    )
    if not errors:
        return (
            "finalize_waiting_review"
            if status == "DESIGN_EXPLORATION"
            else "continue_waiting_for_user"
        )
    if state.get("exploration_generation_attempt", 0) >= MAXIMUM_GENERATION_ATTEMPTS:
        return "wait_for_user_after_retry_exhausted"
    if all(error.startswith("缺少") for error in errors):
        return "resume_missing_artifacts"
    return "start_new_round_preserve_invalid_round"


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="校验产品探索预览轮次")
    parser.add_argument("project_yaml", type=Path)
    args = parser.parse_args()
    try:
        state = load_project_state(args.project_yaml)
    except (OSError, ProjectStateError) as exc:
        print(f"FAIL: {exc}")
        return 1
    reference = state.get("active_design_preview_round")
    if not reference:
        print("FAIL: active_design_preview_round 为空")
        return 1
    preview_mode = str(state.get("design_preview_mode") or DESIGN_PREVIEW_MODE_LEGACY)
    errors = validate_preview_round(
        args.project_yaml.parent, reference, preview_mode=preview_mode
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"PASS: design preview round is valid ({preview_mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
