"""Browser Evidence 与 Browser Gate 的组合工具。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .models import BrowserProfile


def merge_browser_evidence(
    manifest: Mapping[str, Any], bundle: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """把 Browser Run/Step 追加到现有 Manifest，不覆盖既有证据。"""

    result = deepcopy(dict(manifest))
    for key in ("browser_runs", "browser_evidence"):
        current = result.get(key, [])
        additions = bundle.get(key, [])
        if not isinstance(current, list) or not isinstance(additions, list):
            raise ValueError(f"{key} 必须是列表")
        result[key] = current + deepcopy(additions)
    return result


def evaluate_browser_gate(
    profile: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """按 Profile 确定性计算 Browser Gate 输入。"""

    parsed = BrowserProfile.from_mapping(profile)
    if not parsed.required:
        return {
            "result": "SKIPPED",
            "reason": "browser_not_required_by_profile",
            "evidence_refs": [],
        }
    runs = manifest.get("browser_runs", [])
    steps = manifest.get("browser_evidence", [])
    if not isinstance(runs, list) or not isinstance(steps, list) or not runs:
        return {
            "result": "FAIL",
            "reason": "required_browser_run_not_executed",
            "evidence_refs": [],
        }
    run_refs = [item.get("browser_run_id") for item in runs if isinstance(item, dict)]
    step_refs = [item.get("step_id") for item in steps if isinstance(item, dict)]
    refs = [item for item in run_refs + step_refs if isinstance(item, str)]
    blocked = [
        item
        for item in runs
        if isinstance(item, dict) and item.get("result") == "BLOCKED"
    ]
    if blocked:
        return {
            "result": "BLOCKED",
            "reason": "evaluation_environment_blocked",
            "evidence_refs": refs,
        }
    failed = [
        item
        for item in runs
        if isinstance(item, dict) and item.get("result") != "PASS"
    ]
    failed.extend(
        item
        for item in steps
        if isinstance(item, dict) and item.get("result") != "PASS"
    )
    if failed:
        return {
            "result": "FAIL",
            "reason": "browser_acceptance_failed",
            "evidence_refs": refs,
        }
    executed_scenarios = {
        item.get("scenario_id")
        for item in runs
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }
    missing = [
        item for item in parsed.required_scenarios if item not in executed_scenarios
    ]
    if missing:
        return {
            "result": "FAIL",
            "reason": "required_browser_scenarios_missing:" + ",".join(missing),
            "evidence_refs": refs,
        }
    action_steps = [
        item
        for item in steps
        if isinstance(item, dict) and item.get("action") not in {"start", "close"}
    ]
    if not action_steps:
        return {
            "result": "FAIL",
            "reason": "required_browser_actions_missing",
            "evidence_refs": refs,
        }
    return {"result": "PASS", "reason": None, "evidence_refs": refs}
