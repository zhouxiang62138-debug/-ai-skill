"""Profile-bound Candidate v2 与 legacy read-only 兼容测试。"""

from __future__ import annotations

import pytest

from scripts.best_candidate import append_candidate, load_candidate
from scripts.project_state import ProjectStateError


def _candidate() -> dict[str, object]:
    return {
        "schema_version": 2,
        "candidate_id": "candidate-001",
        "snapshot_id": "snapshot-001",
        "evaluation_id": "evaluation-001",
        "project_revision": 1,
        "score": "9.0",
        "blocking_issues": [],
        "critical_issues": [],
        "regression_status": "PASS",
        "feature_completeness": "9.0",
        "browser_acceptance": "PASS",
        "validated_at": "2026-08-09T00:00:00+08:00",
        "evaluation_profile": "web_app",
        "evaluation_profile_hash": "a" * 64,
        "rubric_version": "web-app-v2",
        "evaluator_model": "gpt-5",
        "calibration_suite_version": "blind-v1",
        "required_gate_results": {
            "GATE-BROWSER-ACCEPTANCE": "PASS",
            "GATE-REGRESSION": "PASS",
        },
    }


def test_web_candidate_skipped_is_not_restorable(tmp_path) -> None:
    candidate = _candidate()
    candidate["browser_acceptance"] = "SKIPPED"
    candidate["required_gate_results"] = {"GATE-BROWSER-ACCEPTANCE": "SKIPPED"}
    with pytest.raises(ProjectStateError):
        append_candidate(tmp_path, candidate)


def test_new_candidate_v1_is_rejected_as_legacy_read_only(tmp_path) -> None:
    candidate = _candidate()
    candidate["schema_version"] = 1
    for field in (
        "evaluation_profile",
        "evaluation_profile_hash",
        "rubric_version",
        "evaluator_model",
        "calibration_suite_version",
        "required_gate_results",
    ):
        candidate.pop(field)
    with pytest.raises(ProjectStateError, match="legacy"):
        append_candidate(tmp_path, candidate)


def test_v2_restore_requires_current_profile_binding_and_all_gates(tmp_path) -> None:
    candidate = _candidate()
    append_candidate(tmp_path, candidate)
    with pytest.raises(ProjectStateError, match="Profile"):
        load_candidate(tmp_path, "candidate-001")
    incomplete = dict(candidate)
    incomplete["required_gate_results"] = {"GATE-BROWSER-ACCEPTANCE": "PASS"}
    with pytest.raises(ProjectStateError):
        append_candidate(tmp_path / "incomplete", incomplete)


def test_candidate_profile_hash_is_checked_on_restore(tmp_path) -> None:
    append_candidate(tmp_path, _candidate())
    with pytest.raises(ProjectStateError, match="evaluation_profile_hash"):
        load_candidate(
            tmp_path,
            "candidate-001",
            evaluation_profile="web_app",
            evaluation_profile_hash="b" * 64,
            rubric_version="web-app-v2",
            calibration_suite_version="blind-v1",
        )
