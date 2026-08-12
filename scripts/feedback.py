"""F5 产品探索反馈识别、状态迁移和方案版本门禁。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from project_state import ProjectStateError, validate_project_state


CONCEPT_TOKEN_MAP = {
    "A": "01",
    "B": "02",
    "C": "03",
    "1": "01",
    "2": "02",
    "3": "03",
    "01": "01",
    "02": "02",
    "03": "03",
    "一": "01",
    "二": "02",
    "三": "03",
}

REJECT_ALL_SIGNALS = (
    "三个都不喜欢",
    "三个都不满意",
    "全部不喜欢",
    "全部不满意",
    "都不喜欢",
    "都不满意",
    "全部否定",
    "重新给我三个",
    "重新生成三",
    "全都不要",
)
RESTORE_SIGNALS = ("恢复", "返回上一", "回到上一", "之前的方案", "以前的方案")
DISCUSSION_SIGNALS = (
    "还不确定",
    "继续讨论",
    "先看看",
    "分别适合",
    "哪个成本",
    "哪一个成本",
    "对比一下",
    "解释一下",
    "有什么区别",
    "怎么样",
)
MODIFICATION_SIGNALS = (
    "修改",
    "调整",
    "增加",
    "减少",
    "保留",
    "不要深色",
    "不要动画",
    "但",
)
NEW_PREVIEW_SIGNALS = (
    "重新生成",
    "新一轮",
    "修改后再看",
    "调整后再看",
    "继续对比",
    "再给我看",
    "生成预览",
)
SELECTION_SIGNALS = ("选择", "我选", "就按", "采用", "选方案", "用方案")
BLEND_SIGNALS = ("混搭", "混合", "融合", "结合", "首页用", "统计页用", "配色用", "交互用")
PROTOTYPE_CONFIRM_SIGNALS = (
    "确认这个设计",
    "确认当前设计",
    "这个设计可以定稿",
    "按这个设计定稿",
    "高保真方案通过",
    "预览确认",
)
DESIGN_PREVIEW_MODE_LEGACY = "legacy_full"
DESIGN_PREVIEW_MODE_COMPARISON = "direction_comparison"
DESIGN_PREVIEW_MODE_SELECTED = "selected_prototype"
EXCLUDED_REFERENCE_PATTERN = (
    r"(?:不要|排除|不用|不选|不选择)\s*"
    r"(?:方案|概念|concept[_\s-]*)\s*"
    r"(A|B|C|01|02|03|1|2|3|一|二|三)"
)


@dataclass(frozen=True)
class FeedbackDecision:
    action: str
    concept_refs: tuple[str, ...]
    excluded_refs: tuple[str, ...]
    raw_text: str
    requires_new_preview: bool = False
    reason: str | None = None


def _concept_ref(round_reference: str, token: str) -> str:
    number = CONCEPT_TOKEN_MAP[token.upper() if token.upper() in CONCEPT_TOKEN_MAP else token]
    return f"{round_reference}/concept_{number}"


def _extract_relative_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    patterns = (
        r"(?:方案|概念|concept[_\s-]*)\s*(A|B|C|01|02|03|1|2|3|一|二|三)",
        r"第(一|二|三|1|2|3)个(?:方案|概念)?",
        r"(?<![A-Za-z0-9])([ABC])(?=\s*(?:的|、|,|，|和|与|\+))",
    )
    for pattern in patterns:
        tokens.extend(match.group(1) for match in re.finditer(pattern, text, re.I))
    return tokens


def extract_concept_refs(text: str, current_round: str) -> tuple[str, ...]:
    full_reference_pattern = (
        r"artifacts/design_previews/round_\d{3}/concept_0[1-3]"
    )
    refs = re.findall(full_reference_pattern, text)
    relative_text = re.sub(full_reference_pattern, "", text)
    refs.extend(
        _concept_ref(current_round, token)
        for token in _extract_relative_tokens(relative_text)
    )
    return tuple(dict.fromkeys(refs))


def _extract_excluded_refs(text: str, current_round: str) -> tuple[str, ...]:
    tokens = re.findall(EXCLUDED_REFERENCE_PATTERN, text, re.I)
    return tuple(dict.fromkeys(_concept_ref(current_round, token) for token in tokens))


def classify_feedback(
    text: str,
    current_round: str,
    *,
    preview_mode: str = DESIGN_PREVIEW_MODE_COMPARISON,
) -> FeedbackDecision:
    raw = text.strip()
    if not raw:
        return FeedbackDecision("ambiguous", (), (), text, reason="empty_feedback")
    if not re.fullmatch(r"artifacts/design_previews/round_\d{3}", current_round):
        raise ProjectStateError("current_round 格式无效")

    refs = extract_concept_refs(raw, current_round)
    excluded = _extract_excluded_refs(raw, current_round)
    positive_text = re.sub(EXCLUDED_REFERENCE_PATTERN, "", raw, flags=re.I)
    positive_tokens = re.findall(
        r"(?:选择|我选|采用|用|按)\s*(?:方案|概念)?\s*"
        r"(A|B|C|01|02|03|1|2|3|一|二|三)",
        positive_text,
        re.I,
    )
    positive_refs = {
        _concept_ref(current_round, token) for token in positive_tokens
    }
    if positive_refs & set(excluded):
        return FeedbackDecision(
            "conflicting", refs, excluded, raw, reason="selected_and_excluded_same_concept"
        )
    refs = tuple(reference for reference in refs if reference not in set(excluded))
    if any(signal in raw for signal in REJECT_ALL_SIGNALS):
        return FeedbackDecision("reject_all", (), excluded, raw)
    if preview_mode == DESIGN_PREVIEW_MODE_SELECTED:
        if any(signal in raw for signal in PROTOTYPE_CONFIRM_SIGNALS):
            return FeedbackDecision("prototype_confirmed", (), excluded, raw)
        if any(signal in raw for signal in MODIFICATION_SIGNALS):
            return FeedbackDecision(
                "prototype_modify", (), excluded, raw, requires_new_preview=True
            )

    restore = any(signal in raw for signal in RESTORE_SIGNALS)
    discuss = any(signal in raw for signal in DISCUSSION_SIGNALS) or "?" in raw or "？" in raw
    modify = any(signal in raw for signal in MODIFICATION_SIGNALS)
    new_preview = any(signal in raw for signal in NEW_PREVIEW_SIGNALS)
    select = any(signal in raw for signal in SELECTION_SIGNALS)
    blend = any(signal in raw for signal in BLEND_SIGNALS)

    if restore:
        if refs:
            return FeedbackDecision("restore", refs, excluded, raw)
        return FeedbackDecision("ambiguous", (), excluded, raw, reason="restore_target_missing")
    if len(refs) >= 2 and discuss and not blend:
        return FeedbackDecision("discuss", refs, excluded, raw)
    if len(refs) >= 2:
        return FeedbackDecision("blend", refs, excluded, raw)
    if len(refs) == 1 and modify:
        return FeedbackDecision(
            "modify", refs, excluded, raw, requires_new_preview=new_preview
        )
    if len(refs) == 1 and select:
        return FeedbackDecision("single", refs, excluded, raw)
    if discuss:
        return FeedbackDecision("discuss", refs, excluded, raw)
    if blend or modify or select:
        return FeedbackDecision(
            "ambiguous", refs, excluded, raw, reason="concept_reference_missing"
        )
    return FeedbackDecision("ambiguous", refs, excluded, raw, reason="intent_not_recognized")


def _require_record(reference: str, pattern: str, label: str) -> None:
    if not re.fullmatch(pattern, reference):
        raise ProjectStateError(f"{label} 路径格式无效")


def _round_number(reference: str) -> int:
    match = re.fullmatch(r"artifacts/design_previews/round_(\d{3})", reference)
    if not match:
        raise ProjectStateError("设计预览轮次路径格式无效")
    return int(match.group(1))


def _validate_concept_refs(refs: tuple[str, ...]) -> None:
    if not refs:
        raise ProjectStateError("选择类反馈必须引用至少一个概念")
    for reference in refs:
        if not re.fullmatch(
            r"artifacts/design_previews/round_\d{3}/concept_0[1-3]", reference
        ):
            raise ProjectStateError(f"概念引用格式无效：{reference}")


def _prepare_new_round(
    updated: dict[str, Any],
    next_round_reference: str,
    *,
    preview_mode: str,
    preserve_selection: bool = False,
) -> None:
    current_reference = updated.get("active_design_preview_round")
    if not current_reference:
        raise ProjectStateError("当前设计预览轮次为空")
    current_number = _round_number(current_reference)
    next_number = _round_number(next_round_reference)
    if next_number != current_number + 1:
        raise ProjectStateError("新设计预览轮次必须在当前轮次基础上递增 1")
    updated.update(
        {
            "status": "DESIGN_EXPLORATION",
            "next_role": "planner",
            "design_review_status": "revision_requested",
            "design_preview_mode": preview_mode,
            "design_preview_round": next_number,
            "active_design_preview_round": next_round_reference,
            "exploration_generation_attempt": 0,
            "active_plan": None,
            "approved_proposal": None,
        }
    )
    if not preserve_selection:
        updated["selected_design_concept"] = None
        updated["design_selection_record"] = None


def apply_feedback_decision(
    state: dict[str, Any],
    decision: FeedbackDecision,
    *,
    feedback_record: str,
    selection_record: str | None = None,
    next_round_reference: str | None = None,
) -> dict[str, Any]:
    """把结构化反馈应用为候选状态，不写文件或批准产品。"""

    if state.get("status") != "WAITING_FOR_DESIGN_REVIEW":
        raise ProjectStateError("只有 WAITING_FOR_DESIGN_REVIEW 可以接收设计反馈")
    _require_record(
        feedback_record,
        r"memory/decisions/design-feedback-\d{3}\.md",
        "feedback_record",
    )
    updated = copy.deepcopy(state)
    updated["exploration_feedback_record"] = feedback_record
    updated["design_feedback_round"] = state.get("design_feedback_round", 0) + 1
    preview_mode = str(state.get("design_preview_mode") or DESIGN_PREVIEW_MODE_LEGACY)

    if decision.action in {"single", "modify", "blend", "restore"}:
        _validate_concept_refs(decision.concept_refs)
        if preview_mode == DESIGN_PREVIEW_MODE_COMPARISON:
            if not selection_record:
                raise ProjectStateError("方向选择必须创建 design_selection_record")
            if not next_round_reference:
                raise ProjectStateError("选择方向后必须提供单一高保真预览的下一轮路径")
            _require_record(
                selection_record,
                r"memory/decisions/design-selection-\d{3}\.md",
                "selection_record",
            )
            mode = {
                "single": "single",
                "modify": "modified",
                "blend": "blend",
                "restore": "restored",
            }[decision.action]
            updated.update(
                {
                    "selected_design_concept": {
                        "mode": mode,
                        "concept_refs": list(decision.concept_refs),
                        "integration_notes": decision.raw_text,
                    },
                    "design_selection_record": selection_record,
                }
            )
            _prepare_new_round(
                updated,
                next_round_reference,
                preview_mode=DESIGN_PREVIEW_MODE_SELECTED,
                preserve_selection=True,
            )
            updated["design_feedback_status"] = "selected_prototype_requested"
        elif decision.action == "modify" and decision.requires_new_preview:
            if not next_round_reference:
                raise ProjectStateError("要求查看修改预览时必须提供下一轮路径")
            _prepare_new_round(
                updated,
                next_round_reference,
                preview_mode=preview_mode,
            )
            updated["design_feedback_status"] = "modification_requested"
        else:
            if not selection_record:
                raise ProjectStateError("方向选择必须创建 design_selection_record")
            _require_record(
                selection_record,
                r"memory/decisions/design-selection-\d{3}\.md",
                "selection_record",
            )
            mode = {
                "single": "single",
                "modify": "modified",
                "blend": "blend",
                "restore": "restored",
            }[decision.action]
            updated.update(
                {
                    "status": "PLANNING_REVISION",
                    "next_role": "planner",
                    "design_review_status": "direction_selected",
                    "design_feedback_status": {
                        "single": "direction_selected",
                        "modify": "modification_requested",
                        "blend": "blend_selected",
                        "restore": "direction_selected",
                    }[decision.action],
                    "selected_design_concept": {
                        "mode": mode,
                        "concept_refs": list(decision.concept_refs),
                        "integration_notes": decision.raw_text,
                    },
                    "design_selection_record": selection_record,
                    "active_plan": None,
                    "approved_proposal": None,
                }
            )
    elif decision.action == "prototype_confirmed":
        if preview_mode != DESIGN_PREVIEW_MODE_SELECTED:
            raise ProjectStateError("只有单一高保真预览可以被确认")
        if not state.get("selected_design_concept") or not state.get("design_selection_record"):
            raise ProjectStateError("确认高保真预览前必须保留方向选择来源")
        updated.update(
            {
                "status": "PLANNING_REVISION",
                "next_role": "planner",
                "design_review_status": "direction_selected",
                "design_feedback_status": "prototype_confirmed",
                "active_plan": None,
                "approved_proposal": None,
            }
        )
    elif decision.action == "prototype_modify":
        if preview_mode != DESIGN_PREVIEW_MODE_SELECTED:
            raise ProjectStateError("prototype_modify 只适用于单一高保真预览")
        if not next_round_reference:
            raise ProjectStateError("修改高保真预览时必须提供下一轮路径")
        if not selection_record:
            raise ProjectStateError("修改高保真方向必须创建新的 design_selection_record")
        _require_record(
            selection_record,
            r"memory/decisions/design-selection-\d{3}\.md",
            "selection_record",
        )
        updated["design_selection_record"] = selection_record
        selected = copy.deepcopy(state.get("selected_design_concept"))
        if not isinstance(selected, dict):
            raise ProjectStateError("修改高保真预览前缺少 selected_design_concept")
        selected["mode"] = "modified"
        selected["integration_notes"] = decision.raw_text
        updated["selected_design_concept"] = selected
        _prepare_new_round(
            updated,
            next_round_reference,
            preview_mode=DESIGN_PREVIEW_MODE_SELECTED,
            preserve_selection=True,
        )
        updated["design_feedback_status"] = "modification_requested"
    elif decision.action == "reject_all":
        if not next_round_reference:
            raise ProjectStateError("全部否定后必须提供下一轮路径")
        _prepare_new_round(
            updated,
            next_round_reference,
            preview_mode=DESIGN_PREVIEW_MODE_COMPARISON,
        )
        updated["design_feedback_status"] = "all_rejected"
        reasons = list(updated.get("exploration_trigger_reasons") or [])
        if "user_rejected_all_directions" not in reasons:
            reasons.append("user_rejected_all_directions")
        updated["exploration_trigger_reasons"] = reasons
    elif decision.action in {"discuss", "ambiguous", "conflicting"}:
        selected_design = (
            copy.deepcopy(state.get("selected_design_concept"))
            if preview_mode == DESIGN_PREVIEW_MODE_SELECTED
            else None
        )
        selection_reference = (
            state.get("design_selection_record")
            if preview_mode == DESIGN_PREVIEW_MODE_SELECTED
            else None
        )
        updated.update(
            {
                "status": "WAITING_FOR_DESIGN_REVIEW",
                "next_role": "planner",
                "design_feedback_status": {
                    "discuss": "discussing",
                    "ambiguous": "ambiguous",
                    "conflicting": "conflicting",
                }[decision.action],
                "selected_design_concept": selected_design,
                "design_selection_record": selection_reference,
                "active_plan": None,
                "approved_proposal": None,
            }
        )
    else:
        raise ProjectStateError(f"不支持的反馈动作：{decision.action}")

    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("反馈状态迁移无效：" + "; ".join(errors))
    return updated


def integrate_feedback_into_proposal(
    state: dict[str, Any], *, new_proposal_reference: str
) -> dict[str, Any]:
    """选择整合为完整新方案版本后进入产品审核，仍不构成批准。"""

    if state.get("status") != "PLANNING_REVISION":
        raise ProjectStateError("只有 PLANNING_REVISION 可以整合设计反馈")
    if state.get("design_review_status") != "direction_selected":
        raise ProjectStateError("尚无可整合的明确设计方向")
    if (
        state.get("design_preview_mode") == DESIGN_PREVIEW_MODE_SELECTED
        and state.get("design_feedback_status") != "prototype_confirmed"
    ):
        raise ProjectStateError("单一高保真预览尚未获得用户明确确认")
    match = re.fullmatch(
        r"memory/proposals/product_proposal_v(\d{3})\.md", new_proposal_reference
    )
    if not match:
        raise ProjectStateError("新产品方案路径格式无效")
    expected_version = state.get("proposal_version", 0) + 1
    if int(match.group(1)) != expected_version:
        raise ProjectStateError("新产品方案版本必须严格递增 1")
    if new_proposal_reference == state.get("active_proposal"):
        raise ProjectStateError("不得覆盖当前产品方案")

    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "WAITING_FOR_PRODUCT_REVIEW",
            "next_role": "planner",
            "proposal_status": "waiting_user_review",
            "proposal_version": expected_version,
            "active_proposal": new_proposal_reference,
            "approved_proposal": None,
            "design_review_status": "integrated_into_proposal",
            "design_feedback_status": "integrated_into_proposal",
            "user_approval_status": "waiting_explicit_confirmation",
            "active_plan": None,
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("整合产品方案状态无效：" + "; ".join(errors))
    return updated
