"""F4 产品与设计探索的确定性触发、工件校验和恢复判断。"""

from __future__ import annotations

import argparse
import copy
import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_state import ProjectStateError, load_project_state
from project_state import validate_project_state


REQUIRED_CONCEPTS = ("concept_01", "concept_02", "concept_03")
REQUIRED_FILES = ("concept.md", "preview.html", "preview.css")
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
) -> dict[str, Any]:
    """创建进入 DESIGN_EXPLORATION 的候选状态，不直接写文件。"""

    if decision.action != "exploration_required" or decision.required is not True:
        raise ProjectStateError("只有 exploration_required 决策可以开始探索")
    if state.get("requirements_status") != "sufficient_for_planning":
        raise ProjectStateError("需求未达到 sufficient_for_planning")
    if not state.get("active_requirements"):
        raise ProjectStateError("开始探索前 active_requirements 不能为空")
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
            "active_design_preview_round": round_reference,
            "exploration_generation_attempt": attempt,
            "active_plan": None,
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("无法进入设计探索：" + "; ".join(errors))
    return updated


def finalize_preview_round(
    state: dict[str, Any], project_root: str | Path
) -> dict[str, Any]:
    """只有预览完整有效时才进入 WAITING_FOR_DESIGN_REVIEW。"""

    if state.get("status") != "DESIGN_EXPLORATION":
        raise ProjectStateError("只有 DESIGN_EXPLORATION 可以完成预览轮次")
    reference = state.get("active_design_preview_round")
    if not reference:
        raise ProjectStateError("active_design_preview_round 不能为空")
    preview_errors = validate_preview_round(project_root, reference)
    if preview_errors:
        raise ProjectStateError("预览轮次校验失败：" + "; ".join(preview_errors))
    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "WAITING_FOR_DESIGN_REVIEW",
            "next_role": "planner",
            "design_review_status": "waiting_user_selection",
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


def validate_preview_round(project_root: str | Path, round_reference: str) -> list[str]:
    root = Path(project_root)
    try:
        round_dir = _resolve_inside(root, round_reference)
    except ProjectStateError as exc:
        return [str(exc)]
    if not round_dir.is_dir():
        return [f"缺少设计预览轮次目录：{round_reference}"]

    errors: list[str] = []
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
        for file_name in REQUIRED_FILES:
            file_path = concept_dir / file_name
            if not file_path.is_file():
                errors.append(f"缺少文件：{concept_name}/{file_name}")
            elif not file_path.read_text(encoding="utf-8").strip():
                errors.append(f"文件为空：{concept_name}/{file_name}")

        concept_path = concept_dir / "concept.md"
        if concept_path.is_file():
            sections = _extract_sections(concept_path.read_text(encoding="utf-8"))
            concept_sections[concept_name] = sections
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

    for left, right in itertools.combinations(REQUIRED_CONCEPTS, 2):
        if left not in concept_sections or right not in concept_sections:
            continue
        differences = sum(
            _normalize_difference_text(concept_sections[left].get(section, ""))
            != _normalize_difference_text(concept_sections[right].get(section, ""))
            for section in DISTINCTNESS_SECTIONS
        )
        if differences < 2:
            errors.append(
                f"{left} 与 {right} 的产品路线差异不足："
                "产品定位、核心优势、特色功能、主要用户路径至少两项不同"
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
    errors = validate_preview_round(project_root, round_reference)
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
    errors = validate_preview_round(args.project_yaml.parent, reference)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: design preview round contains three complete product directions")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
