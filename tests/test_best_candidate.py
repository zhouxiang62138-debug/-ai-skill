"""Stage 7 Best Validated Candidate 与 Runtime restore 权限测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.candidates import CandidateRuntimeService
from scripts.best_candidate import (
    append_candidate,
    append_restore_recommendation,
    best_validated_candidate,
    load_candidate,
)
from scripts.project_state import ProjectStateError, parse_project_yaml


def _candidate(
    number: int,
    score: str,
    *,
    feature: str = "8.5",
    browser: str = "PASS",
    blocking: list[str] | None = None,
    critical: list[str] | None = None,
    regression: str = "PASS",
) -> dict:
    return {
        "schema_version": 1,
        "candidate_id": f"candidate-{number:03d}",
        "snapshot_id": f"snapshot-{number:03d}",
        "evaluation_id": f"evaluation-{number:03d}",
        "project_revision": number,
        "score": score,
        "blocking_issues": blocking or [],
        "critical_issues": critical or [],
        "regression_status": regression,
        "feature_completeness": feature,
        "browser_acceptance": browser,
        "validated_at": f"2026-08-09T00:0{number}:00+08:00",
    }


def test_best_candidate_is_not_latest_candidate(tmp_path: Path) -> None:
    append_candidate(tmp_path, _candidate(1, "8.5"))
    append_candidate(tmp_path, _candidate(2, "9.0"))
    append_candidate(tmp_path, _candidate(3, "8.3"))
    best = best_validated_candidate(tmp_path)
    assert best is not None
    assert best["candidate_id"] == "candidate-002"
    assert load_candidate(tmp_path, "candidate-002")["snapshot_id"] == "snapshot-002"


def test_invalid_candidate_cannot_become_best(tmp_path: Path) -> None:
    append_candidate(tmp_path, _candidate(1, "8.5"))
    append_candidate(tmp_path, _candidate(2, "9.9", critical=["EVAL-002-001"]))
    append_candidate(tmp_path, _candidate(3, "9.8", regression="FAIL"))
    append_candidate(tmp_path, _candidate(4, "9.7", feature="7.9"))
    append_candidate(tmp_path, _candidate(5, "9.6", browser="BLOCKED"))
    assert best_validated_candidate(tmp_path)["candidate_id"] == "candidate-001"
    with pytest.raises(ProjectStateError, match="只能恢复满足验证条件"):
        load_candidate(tmp_path, "candidate-002")


def test_evaluator_recommendation_is_append_only_and_does_not_restore(tmp_path: Path) -> None:
    append_candidate(tmp_path, _candidate(1, "8.5"))
    append_candidate(tmp_path, _candidate(2, "9.0"))
    recommendation = append_restore_recommendation(
        tmp_path,
        current_candidate_id="candidate-001",
        created_at="2026-08-09T01:00:00+08:00",
    )
    assert recommendation is not None
    record = parse_project_yaml(recommendation.read_text(encoding="utf-8"))
    assert record["action"] == "recommend_restore_candidate"
    assert record["recommended_candidate_id"] == "candidate-002"
    assert record["no_restore_performed"] is True


def test_runtime_candidate_service_is_the_only_restore_path(tmp_path: Path) -> None:
    append_candidate(tmp_path, _candidate(1, "8.5"))
    calls: list[tuple[object, str]] = []

    class FakeSnapshotService:
        def restore(self, context: object, snapshot_id: str) -> None:
            calls.append((context, snapshot_id))

    context = object()
    result = CandidateRuntimeService(tmp_path).restore(
        context,
        FakeSnapshotService(),
        candidate_id="candidate-001",
    )
    assert result["candidate_id"] == "candidate-001"
    assert calls == [(context, "snapshot-001")]


def test_candidate_history_is_immutable(tmp_path: Path) -> None:
    append_candidate(tmp_path, _candidate(1, "8.5"))
    with pytest.raises(ProjectStateError, match="下一条 Candidate"):
        append_candidate(tmp_path, _candidate(1, "9.0"))
