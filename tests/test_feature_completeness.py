"""Stage 2 Feature Completeness / Anti-Stub 定向测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluation_evidence import evaluate_gates, validate_evidence_manifest  # noqa: E402
from runtime.completeness import (  # noqa: E402
    FeatureFinding,
    FeatureObservation,
    evaluate_feature_completeness,
    scan_project,
)


PROFILE = {
    "feature_completeness": {
        "required": True,
        "minimum_score": 8.0,
        "required_sources": ["static", "runtime", "browser"],
    }
}


def _observations(*, browser_result: str = "PASS") -> list[FeatureObservation]:
    return [
        FeatureObservation(
            "FC-OBS-001",
            "runtime",
            "REQ-001",
            "AC-001",
            "save persists",
            "database contains record",
            "PASS",
            ("ART-001",),
        ),
        FeatureObservation(
            "FC-OBS-002",
            "browser",
            "REQ-001",
            "AC-001",
            "reload keeps record",
            "record remains after reload" if browser_result == "PASS" else "record disappears after reload",
            browser_result,
            ("ART-001",),
        ),
    ]


def test_static_scanner_detects_multiple_stub_signals(tmp_path: Path) -> None:
    code = tmp_path / "code"
    code.mkdir()
    (code / "app.js").write_text(
        """// TODO: connect persistence\nconst data = 'placeholder';\nconst save = () => {};\n""",
        encoding="utf-8",
    )
    findings = scan_project(tmp_path)
    assert {item.category for item in findings} >= {
        "todo_marker",
        "placeholder",
        "empty_callback",
    }
    assert findings[0].finding_id == "FC-FIND-001"
    result = evaluate_feature_completeness(PROFILE, findings, _observations())
    assert result["result"] == "FAIL"
    assert result["reason"] == "incomplete_or_stub_implementation"


def test_clean_complete_evidence_can_pass_feature_gate() -> None:
    result = evaluate_feature_completeness(PROFILE, [], _observations())
    assert result["result"] == "PASS"
    assert result["score"] == 10.0
    gates = evaluate_gates(
        {"GATE-FEATURE-COMPLETENESS": result},
        [{"id": "GATE-FEATURE-COMPLETENESS", "required": True}],
    )
    assert gates[0]["result"] == "PASS"


def test_partial_persistence_is_a_failure_even_without_static_stub() -> None:
    result = evaluate_feature_completeness(
        PROFILE, [], _observations(browser_result="FAIL")
    )
    assert result["result"] == "FAIL"
    assert result["reason"] == "incomplete_or_stub_implementation"


def test_missing_source_and_blocked_environment_are_not_pass() -> None:
    runtime_only = [
        FeatureObservation(
            "FC-OBS-001",
            "runtime",
            "REQ-001",
            "AC-001",
            "save persists",
            "record exists",
            "PASS",
            ("ART-001",),
        )
    ]
    missing = evaluate_feature_completeness(PROFILE, [], runtime_only)
    assert missing["result"] == "FAIL"
    assert "browser" in missing["reason"]
    blocked = evaluate_feature_completeness(
        {"feature_completeness": {"required": True, "required_sources": ["static", "browser"]}},
        [],
        [
            FeatureObservation(
                "FC-OBS-001",
                "browser",
                "REQ-001",
                "AC-001",
                "flow completes",
                "browser unavailable",
                "BLOCKED",
                ("ART-001",),
            )
        ],
    )
    assert blocked["result"] == "BLOCKED"


def test_feature_results_enter_existing_manifest_and_are_cross_checked() -> None:
    findings = [
        FeatureFinding(
            "FC-FIND-001",
            "todo_marker",
            "major",
            "code/app.js",
            1,
            "TODO",
            1.5,
        )
    ]
    observations = _observations()
    summary = evaluate_feature_completeness(PROFILE, findings, observations)
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "created_at": "2026-08-09T00:00:00+08:00",
        "environment": {
            "os": "windows",
            "architecture": "x86_64",
            "python_version": "3.12",
            "working_directory": "code",
        },
        "commands": [],
        "artifacts": [
            {
                "artifact_id": "ART-001",
                "type": "report",
                "path": "evaluation/evidence/evaluation-001/feature.json",
                "linked_issue_ids": [],
                "linked_requirement_ids": [],
            }
        ],
        "checks": [],
        "gates": [],
        "feature_completeness": summary,
        "feature_findings": [item.to_dict() for item in findings],
        "feature_observations": [item.to_dict() for item in observations],
    }
    assert validate_evidence_manifest(manifest) == []
    manifest["feature_completeness"]["finding_refs"] = ["FC-FIND-999"]
    assert any("finding_refs" in error for error in validate_evidence_manifest(manifest))


def test_profile_can_disable_feature_gate() -> None:
    result = evaluate_feature_completeness({"feature_completeness": {"required": False}}, [], [])
    assert result["result"] == "SKIPPED"
