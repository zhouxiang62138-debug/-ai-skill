"""Planner 使用 Reference Synthesis 的只读来源链工具。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.errors import RuntimeValidationError
from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.project_state import load_project_state
from scripts.reference_protocol import assert_valid, validate_reference_synthesis


_DECISION_ID = re.compile(r"\bREFDEC-\d{3}\b")
_SOURCE_ID = re.compile(r"\bREF-\d{3}\b")
_BUCKETS = ("adopt", "adapt", "avoid")


def load_active_reference_synthesis(project_root: str | Path) -> dict[str, Any] | None:
    """只读取 project.yaml 指向的 synthesis，不回读 URL、图片或历史 Finding。"""

    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    pointer = state.get("active_reference_synthesis")
    if pointer in (None, ""):
        return None
    if not isinstance(pointer, str):
        raise RuntimeValidationError("PLANNER_REFERENCE_POINTER_INVALID")
    path = ExecutionPathPolicy().assert_path("planner", root, pointer, operation="read")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeValidationError("PLANNER_REFERENCE_SYNTHESIS_READ_FAILED") from exc
    if not isinstance(value, dict):
        raise RuntimeValidationError("PLANNER_REFERENCE_SYNTHESIS_INVALID")
    assert_valid(validate_reference_synthesis(value), "reference_synthesis")
    return {"synthesis_ref": pointer, "synthesis": value}


def _decisions(synthesis: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    raw = synthesis.get("decisions")
    if not isinstance(raw, Mapping):
        return result
    for bucket in _BUCKETS:
        values = raw.get(bucket, [])
        if isinstance(values, list):
            for decision in values:
                if isinstance(decision, Mapping) and isinstance(decision.get("decision_id"), str):
                    result[str(decision["decision_id"])] = decision
    return result


def _requirement_exclusions(requirements: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("explicit_exclusions", "excluded_domains", "exclusions"):
        raw = requirements.get(key)
        if isinstance(raw, list):
            values.update(str(item) for item in raw)
    for item in requirements.get("references", []) if isinstance(requirements.get("references"), list) else []:
        if isinstance(item, Mapping):
            values.update(str(value) for value in item.get("explicit_exclusions", []) or [])
    return values


def select_reference_decisions(
    synthesis: Mapping[str, Any],
    *,
    adopted: Mapping[str, str] | None = None,
    requirements: Mapping[str, Any] | None = None,
) -> dict[str, list[Mapping[str, Any]]]:
    """按 Planner 的明确选择生成结果；synthesis 本身永远不会被修改或自动采纳。"""

    choices = dict(adopted or {})
    blocked_domains = _requirement_exclusions(requirements or {})
    by_id = _decisions(synthesis)
    result: dict[str, list[Mapping[str, Any]]] = {bucket: [] for bucket in _BUCKETS}
    for decision_id, bucket in choices.items():
        if bucket not in _BUCKETS or decision_id not in by_id:
            raise RuntimeValidationError("PLANNER_REFERENCE_DECISION_INVALID")
        decision = by_id[decision_id]
        if str(decision.get("domain")) in blocked_domains:
            continue
        result[bucket].append(decision)
    return result


def render_reference_integration(
    synthesis: Mapping[str, Any],
    *,
    selections: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> str:
    """渲染 Proposal 必须包含的 Reference Integration 区块。"""

    selections = selections or {bucket: [] for bucket in _BUCKETS}
    lines = ["# Reference Integration", "", "## References Used"]
    lines.extend(f"- {reference_id}" for reference_id in synthesis.get("source_references", []) or [])
    if not synthesis.get("source_references"):
        lines.append("- None")
    for bucket, title in (("adopt", "Adopt"), ("adapt", "Adapt"), ("avoid", "Avoid")):
        lines.extend(["", f"## {title}"])
        values = selections.get(bucket, [])
        if not values:
            lines.append("- None selected")
        for decision in values:
            lines.append(
                f"- {decision['decision_id']} — {decision.get('domain', 'unknown')}: "
                f"{decision.get('rationale', 'Planner decision')}"
            )
    lines.extend(["", "## Unknown / Not Used"])
    unknown = synthesis.get("unknown") or []
    conflicts = synthesis.get("conflicts") or []
    if not unknown and not conflicts:
        lines.append("- None")
    for item in unknown:
        lines.append(f"- Unknown — {item.get('domain', 'unknown')}: {item.get('reason', 'insufficient evidence')}")
    for item in conflicts:
        lines.append(f"- Not Used pending Planner resolution — {item.get('conflict_id', 'REFCON-unknown')}: {item.get('statement', '')}")
    lines.extend([
        "",
        "## Traceability",
        "- Proposal decision -> REFDEC -> REFFND -> REF source; synthesis is evidence, not automatic adoption.",
        "- Explicit user requirements and exclusions override every reference decision.",
    ])
    return "\n".join(lines) + "\n"


def validate_proposal_reference_traceability(
    proposal_text: str,
    synthesis: Mapping[str, Any],
) -> list[str]:
    """检查 Proposal 是否保留 Reference Integration 和合法 REFDEC 链接。"""

    errors: list[str] = []
    if not isinstance(proposal_text, str) or "# Reference Integration" not in proposal_text:
        errors.append("Proposal 缺少 # Reference Integration")
        return errors
    known_sources = {str(item) for item in synthesis.get("source_references", []) or []}
    mentioned_sources = set(_SOURCE_ID.findall(proposal_text))
    if not known_sources.issubset(mentioned_sources):
        errors.append("References Used 未覆盖 active_reference_synthesis 的全部来源")
    known_decisions = set(_decisions(synthesis))
    for decision_id in set(_DECISION_ID.findall(proposal_text)):
        if decision_id not in known_decisions:
            errors.append(f"Proposal 引用了未知的 {decision_id}")
    if "Proposal decision -> REFDEC -> REFFND" not in proposal_text:
        errors.append("Proposal 缺少 Proposal -> REFDEC -> REFFND -> Reference 追溯声明")
    return errors
