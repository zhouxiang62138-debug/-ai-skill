"""Browser Evidence 与 Browser Gate 的组合工具。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import parse_project_yaml

from .errors import BrowserPolicyError
from .models import BrowserProfile, BrowserScenarioManifest


def load_scenario_manifest(
    profile: Mapping[str, Any] | BrowserProfile,
    project_root: str | Path,
) -> BrowserScenarioManifest:
    """由 Runtime 按受保护路径加载 Profile 声明的场景清单。"""

    parsed = profile if isinstance(profile, BrowserProfile) else BrowserProfile.from_mapping(profile)
    reference = parsed.scenario_manifest_reference
    if not reference:
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_REQUIRED")
    normalized = reference.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/")[0] or ".." in normalized.split("/"):
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_PATH_INVALID")
    root = Path(project_root).resolve()
    unresolved = root / reference
    cursor = unresolved
    while cursor != root:
        if cursor.is_symlink():
            raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_SYMLINK_ESCAPE")
        cursor = cursor.parent
    target = unresolved.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_PATH_ESCAPE") from exc
    if not target.is_file():
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_MISSING")
    try:
        raw = parse_project_yaml(target.read_text(encoding="utf-8"))
        manifest = BrowserScenarioManifest.from_mapping(raw)
    except Exception as exc:
        if isinstance(exc, BrowserPolicyError):
            raise
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_INVALID") from exc
    if manifest.profile != parsed.scenario_manifest_reference.split("/")[-1].split(".")[0] and manifest.profile != "web_app":
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_PROFILE_MISMATCH")
    if parsed.scenario_manifest_hash and manifest.compute_hash() != parsed.scenario_manifest_hash:
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_HASH_MISMATCH")
    missing = set(parsed.required_scenarios) - {item.scenario_id for item in manifest.scenarios}
    if missing:
        raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_REQUIRED_SCENARIO_MISSING")
    return manifest


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
    profile: Mapping[str, Any],
    manifest: Mapping[str, Any],
    scenario_manifest: Mapping[str, Any] | BrowserScenarioManifest | None = None,
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """按 Profile 和场景清单确定性计算 Browser Gate 输入。"""

    parsed = BrowserProfile.from_mapping(profile)
    if not parsed.required:
        return {
            "result": "SKIPPED",
            "reason": "browser_not_required_by_profile",
            "evidence_refs": [],
        }
    parsed_manifest: BrowserScenarioManifest | None = None
    if scenario_manifest is None and parsed.scenario_manifest_reference:
        if project_root is None:
            raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_REQUIRED")
        scenario_manifest = load_scenario_manifest(parsed, project_root)
    if scenario_manifest is not None:
        parsed_manifest = (
            scenario_manifest
            if isinstance(scenario_manifest, BrowserScenarioManifest)
            else BrowserScenarioManifest.from_mapping(scenario_manifest)
        )
        if parsed_manifest.artifact_kind in {"template", "example"}:
            raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_TEMPLATE_NOT_APPROVED")
        if parsed.scenario_manifest_hash and parsed_manifest.compute_hash() != parsed.scenario_manifest_hash:
            raise BrowserPolicyError("BROWSER_SCENARIO_MANIFEST_HASH_MISMATCH")
    required_scenarios = set(parsed.required_scenarios)
    if not required_scenarios and parsed_manifest is not None:
        required_scenarios = {item.scenario_id for item in parsed_manifest.scenarios}
    if not required_scenarios:
        return {
            "result": "FAIL",
            "reason": "required_browser_scenarios_missing",
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
    missing = sorted(required_scenarios - executed_scenarios)
    if missing:
        return {
            "result": "FAIL",
            "reason": "required_browser_scenarios_missing:" + ",".join(missing),
            "evidence_refs": refs,
        }
    action_steps = [
        item
        for item in steps
        if isinstance(item, dict)
        and item.get("action") in {"click", "fill", "select", "keyboard"}
    ]
    if not action_steps:
        return {
            "result": "FAIL",
            "reason": "required_browser_actions_missing",
            "evidence_refs": refs,
        }
    if parsed_manifest is not None:
        by_run = {
            item.get("browser_run_id"): item
            for item in runs
            if isinstance(item, dict)
        }
        steps_by_run: dict[str, list[dict[str, Any]]] = {}
        for item in steps:
            if isinstance(item, dict) and isinstance(item.get("browser_run_id"), str):
                steps_by_run.setdefault(item["browser_run_id"], []).append(item)
        for scenario in parsed_manifest.scenarios:
            if scenario.scenario_id not in required_scenarios:
                continue
            matching_runs = [
                run
                for run_id, run in by_run.items()
                if run.get("scenario_id") == scenario.scenario_id
                and run_id in steps_by_run
            ]
            mapped_steps = [
                step
                for run in matching_runs
                for step in steps_by_run.get(run.get("browser_run_id"), [])
                if step.get("requirement_id") == scenario.requirement_id
                and step.get("acceptance_criterion_id") == scenario.acceptance_criterion_id
                and step.get("action") in {"click", "fill", "select", "keyboard"}
            ]
            if not matching_runs or not mapped_steps:
                return {
                    "result": "FAIL",
                    "reason": f"scenario_business_trace_missing:{scenario.scenario_id}",
                    "evidence_refs": refs,
                }
            expected_actions = [str(item.get("action")) for item in scenario.steps]
            actual_actions = [
                str(step.get("action"))
                for step in steps_by_run.get(matching_runs[0].get("browser_run_id"), [])
                if step.get("action") not in {"start", "close", "screenshot"}
            ]
            if actual_actions != expected_actions:
                return {
                    "result": "FAIL",
                    "reason": f"scenario_step_order_invalid:{scenario.scenario_id}",
                    "evidence_refs": refs,
                }
            ui_observations = {"inspect_dom_state", "read_visible_text", "inspect_url"}
            if not any(step.get("action") in ui_observations for step in steps_by_run.get(matching_runs[0].get("browser_run_id"), [])):
                return {
                    "result": "FAIL",
                    "reason": f"scenario_ui_evidence_missing:{scenario.scenario_id}",
                    "evidence_refs": refs,
                }
            api_evidence = manifest.get("api_state_evidence", [])
            if not isinstance(api_evidence, list) or not api_evidence:
                return {
                    "result": "FAIL",
                    "reason": f"scenario_api_evidence_missing:{scenario.scenario_id}",
                    "evidence_refs": refs,
                }
    return {"result": "PASS", "reason": None, "evidence_refs": refs}
