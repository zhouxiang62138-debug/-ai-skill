"""F6 产品批准、正式规格、Plan 独立批准与 Generator 门禁。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from .project_state import ProjectStateError, validate_project_state
except ImportError:  # 兼容 tests 直接把 scripts 加入 sys.path
    from project_state import ProjectStateError, validate_project_state


PRODUCT_SPEC_SECTIONS = (
    "版本与获批来源",
    "产品名称",
    "产品目标",
    "目标用户",
    "核心使用场景",
    "最终产品方向",
    "最终视觉风格",
    "页面列表",
    "页面职责",
    "主要用户流程",
    "核心功能",
    "非核心功能",
    "MVP 范围",
    "明确不做的内容",
    "数据需求",
    "交互规则",
    "错误与空状态",
    "响应式或多端要求",
    "无障碍要求",
    "性能要求",
    "隐私与安全要求",
    "产品验收标准",
    "后续扩展方向",
    "已批准的设计决策",
    "尚未确定但不阻塞开发的事项",
)

PLAN_SECTIONS = (
    "获批来源",
    "目标与范围",
    "技术栈",
    "项目结构",
    "架构设计",
    "数据模型",
    "页面与组件拆分",
    "API 或服务设计",
    "技术约束",
    "开发阶段",
    "实现任务与输入输出",
    "文件修改范围",
    "Generator 执行顺序",
    "测试要求",
    "验收标准",
    "Evaluator 验收方式",
    "风险",
    "回滚方式",
    "尚待 Plan 审核确认的事项",
)

PRODUCT_APPROVE_PATTERNS = (
    r"确认(?:当前|这个|该)?产品方案",
    r"批准(?:当前|这个|该)?产品方案",
    r"确认(?:当前|这个|该)?方案",
)
PLAN_APPROVE_PATTERNS = (
    r"确认(?:当前|这个|该)?(?:开发|实施)?\s*(?:Plan|计划)",
    r"批准(?:当前|这个|该)?(?:开发|实施)?\s*(?:Plan|计划)",
    r"按(?:当前|这个|该)?\s*(?:Plan|计划)开始开发",
)
REVISION_SIGNALS = ("修改", "调整", "还要改", "先别确认", "暂不确认", "不批准")
REVOCATION_SIGNALS = ("撤销", "收回批准", "取消批准", "取消确认")
AMBIGUOUS_POSITIVE_SIGNALS = (
    "看起来不错",
    "还可以",
    "比较好",
    "比较喜欢",
    "大概这样",
    "开始开发",
)


@dataclass(frozen=True)
class ApprovalDecision:
    target: str
    action: str
    raw_text: str
    reason: str | None = None


def classify_approval(
    text: str,
    target: str,
    *,
    responding_to_explicit_question: bool = False,
) -> ApprovalDecision:
    """区分产品批准与 Plan 批准，模糊正面表达不放行。"""

    if target not in {"product", "plan"}:
        raise ProjectStateError("批准目标必须是 product 或 plan")
    raw = text.strip()
    if not raw:
        return ApprovalDecision(target, "ambiguous", text, "empty_response")
    if any(signal in raw for signal in REVOCATION_SIGNALS):
        return ApprovalDecision(target, "revoke", raw)
    if any(signal in raw for signal in REVISION_SIGNALS):
        return ApprovalDecision(target, "revise", raw)

    patterns = PRODUCT_APPROVE_PATTERNS if target == "product" else PLAN_APPROVE_PATTERNS
    if any(re.search(pattern, raw, re.I) for pattern in patterns):
        return ApprovalDecision(target, "approve", raw)
    if responding_to_explicit_question and raw in {
        "确认",
        "是",
        "可以",
        "同意",
        "批准",
        "确认批准",
    }:
        return ApprovalDecision(target, "approve", raw, "explicit_question_response")
    if "?" in raw or "？" in raw:
        return ApprovalDecision(target, "discuss", raw)
    if any(signal in raw for signal in AMBIGUOUS_POSITIVE_SIGNALS):
        return ApprovalDecision(target, "ambiguous", raw, "positive_but_not_explicit")
    return ApprovalDecision(target, "ambiguous", raw, "intent_not_recognized")


def _extract_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def validate_markdown_artifact(
    content: str, required_sections: Iterable[str], label: str
) -> list[str]:
    sections = _extract_sections(content)
    return [
        f"{label} 缺少有效章节：{section}"
        for section in required_sections
        if not sections.get(section)
    ]


def _resolve_project_file(root: str | Path, reference: str) -> Path:
    project_root = Path(root).resolve()
    candidate = (project_root / reference).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ProjectStateError(f"工件路径指向项目目录之外：{reference}") from exc
    if not candidate.is_file():
        raise ProjectStateError(f"工件不存在：{reference}")
    return candidate


def _read_artifact(root: str | Path, reference: str) -> str:
    return _resolve_project_file(root, reference).read_text(encoding="utf-8")


def _validate_references(
    content: str, references: Iterable[str | None], label: str
) -> list[str]:
    return [
        f"{label} 未引用来源：{reference}"
        for reference in references
        if reference and reference not in content
    ]


def _require_reference(reference: str, pattern: str, label: str) -> None:
    if not re.fullmatch(pattern, reference):
        raise ProjectStateError(f"{label} 路径格式无效")


def _design_source(state: dict[str, Any]) -> str:
    if state.get("design_exploration_required") is True:
        reference = state.get("design_selection_record")
        if not reference:
            raise ProjectStateError("设计探索路径缺少 design_selection_record")
        return reference
    reference = state.get("design_skip_record")
    if not reference:
        raise ProjectStateError("跳过设计探索路径缺少 design_skip_record")
    return reference


def prepare_product_approval_for_plan_review(
    state: dict[str, Any],
    decision: ApprovalDecision,
    *,
    product_approval_record: str,
    product_spec_reference: str,
    plan_reference: str,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """产品批准后生成正式规格和待审核 Plan，不能直接进入 Generator。"""

    if state.get("status") != "WAITING_FOR_PRODUCT_REVIEW":
        raise ProjectStateError("只有 WAITING_FOR_PRODUCT_REVIEW 可以批准产品方案")
    if decision.target != "product" or decision.action != "approve":
        raise ProjectStateError("缺少明确的产品方案批准")
    _require_reference(
        product_approval_record,
        r"memory/decisions/product-approval-\d{3}\.md",
        "product_approval_record",
    )
    _require_reference(
        product_spec_reference,
        r"memory/specifications/product_spec_v\d{3}\.md",
        "product_spec_reference",
    )
    _require_reference(
        plan_reference, r"memory/plans/plan-\d{3}\.md", "plan_reference"
    )
    design_source = _design_source(state)
    expected_spec_version = state.get("product_spec_version", 0) + 1
    spec_match = re.search(r"_v(\d{3})\.md$", product_spec_reference)
    if not spec_match or int(spec_match.group(1)) != expected_spec_version:
        raise ProjectStateError("正式产品规格版本必须严格递增 1")
    expected_plan_version = state.get("plan_version", 0) + 1
    plan_match = re.search(r"plan-(\d{3})\.md$", plan_reference)
    if not plan_match or int(plan_match.group(1)) != expected_plan_version:
        raise ProjectStateError("开发 Plan 版本必须严格递增 1")

    if project_root is not None:
        product_approval_content = _read_artifact(
            project_root, product_approval_record
        )
        spec_content = _read_artifact(project_root, product_spec_reference)
        plan_content = _read_artifact(project_root, plan_reference)
        errors = _validate_references(
            product_approval_content,
            (
                state.get("active_requirements"),
                state.get("active_proposal"),
                product_spec_reference,
                plan_reference,
                design_source,
            ),
            "产品批准记录",
        )
        errors.extend(validate_markdown_artifact(
            spec_content, PRODUCT_SPEC_SECTIONS, "正式产品规格"
        ))
        errors.extend(validate_markdown_artifact(plan_content, PLAN_SECTIONS, "开发 Plan"))
        errors.extend(
            _validate_references(
                spec_content,
                (
                    state.get("active_requirements"),
                    state.get("active_proposal"),
                    product_approval_record,
                    state.get("exploration_feedback_record"),
                    design_source,
                ),
                "正式产品规格",
            )
        )
        errors.extend(
            _validate_references(
                plan_content,
                (
                    state.get("active_requirements"),
                    state.get("active_proposal"),
                    product_approval_record,
                    product_spec_reference,
                    design_source,
                ),
                "开发 Plan",
            )
        )
        if errors:
            raise ProjectStateError("; ".join(errors))

    updated = copy.deepcopy(state)
    updated.update(
        {
            "proposal_status": "approved",
            "user_approval_status": "approved",
            "approved_proposal": state.get("active_proposal"),
            "product_approval_record": product_approval_record,
            "product_spec_status": "finalized",
            "product_spec_version": expected_spec_version,
            "active_product_spec": product_spec_reference,
            "plan_status": "waiting_user_review",
            "plan_version": expected_plan_version,
            "active_plan": plan_reference,
            "approved_plan": None,
            "plan_approval_status": "waiting_explicit_confirmation",
            "plan_approval_record": None,
            "status": "WAITING_FOR_PLAN_REVIEW",
            "next_role": "planner",
        }
    )
    errors = validate_project_state(updated, project_root)
    if errors:
        raise ProjectStateError("产品批准状态无效：" + "; ".join(errors))
    return updated


def approve_plan(
    state: dict[str, Any],
    decision: ApprovalDecision,
    *,
    plan_approval_record: str,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    if state.get("status") != "WAITING_FOR_PLAN_REVIEW":
        raise ProjectStateError("只有 WAITING_FOR_PLAN_REVIEW 可以批准开发 Plan")
    if decision.target != "plan" or decision.action != "approve":
        raise ProjectStateError("缺少明确的开发 Plan 批准")
    _require_reference(
        plan_approval_record,
        r"memory/decisions/plan-approval-\d{3}\.md",
        "plan_approval_record",
    )
    if project_root is not None:
        content = _read_artifact(project_root, plan_approval_record)
        errors = _validate_references(
            content,
            (
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("product_approval_record"),
                state.get("active_product_spec"),
                state.get("active_plan"),
                state.get("design_selection_record")
                or state.get("design_skip_record"),
            ),
            "Plan 批准记录",
        )
        if errors:
            raise ProjectStateError("; ".join(errors))

    updated = copy.deepcopy(state)
    updated.update(
        {
            "plan_status": "approved",
            "approved_plan": state.get("active_plan"),
            "plan_approval_status": "approved",
            "plan_approval_record": plan_approval_record,
            "status": "APPROVED_FOR_IMPLEMENTATION",
            "next_role": "generator",
        }
    )
    errors = validate_project_state(updated, project_root)
    if errors:
        raise ProjectStateError("Plan 批准状态无效：" + "; ".join(errors))
    if project_root is not None:
        gate_errors = validate_generator_gate(updated, project_root)
        if gate_errors:
            raise ProjectStateError(
                "完整获批来源链无效：" + "; ".join(gate_errors)
            )
    return updated


def request_plan_revision(
    state: dict[str, Any], decision: ApprovalDecision
) -> dict[str, Any]:
    if state.get("status") != "WAITING_FOR_PLAN_REVIEW":
        raise ProjectStateError("只有待审核 Plan 可以请求修订")
    if decision.target != "plan" or decision.action != "revise":
        raise ProjectStateError("缺少明确的 Plan 修订请求")
    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "PLANNING_REVISION",
            "next_role": "planner",
            "plan_status": "revision_requested",
            "approved_plan": None,
            "plan_approval_status": "not_requested",
            "plan_approval_record": None,
        }
    )
    return updated


def return_revised_plan_for_review(
    state: dict[str, Any],
    *,
    new_plan_reference: str,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    if state.get("status") != "PLANNING_REVISION" or state.get(
        "plan_status"
    ) != "revision_requested":
        raise ProjectStateError("当前状态没有等待修订的 Plan")
    _require_reference(
        new_plan_reference, r"memory/plans/plan-\d{3}\.md", "new_plan_reference"
    )
    match = re.search(r"plan-(\d{3})\.md$", new_plan_reference)
    expected_version = state.get("plan_version", 0) + 1
    if not match or int(match.group(1)) != expected_version:
        raise ProjectStateError("修订 Plan 版本必须严格递增 1")
    if project_root is not None:
        content = _read_artifact(project_root, new_plan_reference)
        errors = validate_markdown_artifact(content, PLAN_SECTIONS, "开发 Plan")
        errors.extend(
            _validate_references(
                content,
                (
                    state.get("active_requirements"),
                    state.get("approved_proposal"),
                    state.get("product_approval_record"),
                    state.get("active_product_spec"),
                    _design_source(state),
                ),
                "开发 Plan",
            )
        )
        if errors:
            raise ProjectStateError("; ".join(errors))
    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "WAITING_FOR_PLAN_REVIEW",
            "next_role": "planner",
            "plan_status": "waiting_user_review",
            "plan_version": expected_version,
            "active_plan": new_plan_reference,
            "approved_plan": None,
            "plan_approval_status": "waiting_explicit_confirmation",
            "plan_approval_record": None,
        }
    )
    errors = validate_project_state(updated)
    if errors:
        raise ProjectStateError("修订 Plan 状态无效：" + "; ".join(errors))
    return updated


def revoke_approval(
    state: dict[str, Any],
    *,
    target: str,
    revocation_record: str,
) -> dict[str, Any]:
    if state.get("status") in {"IMPLEMENTING", "EVALUATING", "ACCEPTED"}:
        raise ProjectStateError("执行开始后必须使用正式变更控制，不能直接撤销覆盖")
    if target not in {"product", "plan"}:
        raise ProjectStateError("撤销目标必须是 product 或 plan")
    _require_reference(
        revocation_record,
        r"memory/decisions/approval-revocation-\d{3}\.md",
        "revocation_record",
    )
    updated = copy.deepcopy(state)
    updated["approval_revocation_record"] = revocation_record
    updated["status"] = "PLANNING_REVISION"
    updated["next_role"] = "planner"
    if target == "product":
        updated.update(
            {
                "proposal_status": "revision_requested",
                "user_approval_status": "revoked",
                "approved_proposal": None,
                "product_spec_status": "superseded",
                "active_product_spec": None,
                "plan_status": "superseded",
                "active_plan": None,
                "approved_plan": None,
                "plan_approval_status": "revoked",
            }
        )
    else:
        updated.update(
            {
                "plan_status": "revision_requested",
                "approved_plan": None,
                "plan_approval_status": "revoked",
            }
        )
    return updated


def request_post_implementation_change(
    state: dict[str, Any], *, change_request_record: str
) -> dict[str, Any]:
    if state.get("status") not in {"IMPLEMENTING", "EVALUATING", "ACCEPTED"}:
        raise ProjectStateError("只有已开始实施或验收的项目需要正式变更控制")
    _require_reference(
        change_request_record,
        r"memory/decisions/change-request-\d{3}\.md",
        "change_request_record",
    )
    updated = copy.deepcopy(state)
    updated.update(
        {
            "status": "WAITING_FOR_USER",
            "next_role": None,
            "blocked_reason": "implementation_scope_change_requires_change_control",
            "change_request_record": change_request_record,
        }
    )
    return updated


def validate_generator_gate(
    state: dict[str, Any], project_root: str | Path
) -> list[str]:
    """验证完整获批来源链；不修改状态。"""

    errors = validate_project_state(state, project_root)
    if state.get("status") != "APPROVED_FOR_IMPLEMENTATION":
        errors.append("Generator 只能从 APPROVED_FOR_IMPLEMENTATION 开始")
        return errors
    try:
        design_source = _design_source(state)
        spec_content = _read_artifact(project_root, state["active_product_spec"])
        plan_content = _read_artifact(project_root, state["approved_plan"])
        product_approval = _read_artifact(
            project_root, state["product_approval_record"]
        )
        plan_approval = _read_artifact(project_root, state["plan_approval_record"])
    except (KeyError, ProjectStateError) as exc:
        errors.append(str(exc))
        return errors

    errors.extend(
        validate_markdown_artifact(
            spec_content, PRODUCT_SPEC_SECTIONS, "正式产品规格"
        )
    )
    errors.extend(validate_markdown_artifact(plan_content, PLAN_SECTIONS, "开发 Plan"))
    errors.extend(
        _validate_references(
            product_approval,
            (
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("active_product_spec"),
                state.get("active_plan"),
                design_source,
            ),
            "产品批准记录",
        )
    )
    errors.extend(
        _validate_references(
            spec_content,
            (
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("product_approval_record"),
                design_source,
            ),
            "正式产品规格",
        )
    )
    errors.extend(
        _validate_references(
            plan_content,
            (
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("product_approval_record"),
                state.get("active_product_spec"),
                design_source,
            ),
            "开发 Plan",
        )
    )
    errors.extend(
        _validate_references(
            plan_approval,
            (
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("product_approval_record"),
                state.get("active_product_spec"),
                state.get("approved_plan"),
                design_source,
            ),
            "Plan 批准记录",
        )
    )
    return errors
