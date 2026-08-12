"""RA8 Change Request Reference Integration 测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from change_request import create_change_request  # noqa: E402
from project_state import write_project_state_atomic  # noqa: E402
from reference_change_request import (  # noqa: E402
    list_reference_change_bindings,
    register_reference_change_request,
    revoke_reference_change_binding,
)


def _accepted_state() -> dict[str, object]:
    return {
        "schema_version": 6,
        "project_id": "test_cr_reference_app",
        "project_name": "CR reference fixture",
        "project_type": "application",
        "status": "ACCEPTED",
        "current_iteration": 0,
        "iteration_sequence": 1,
        "automatic_retry_allowed": False,
        "next_role": None,
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "proposal_status": "approved",
        "user_approval_status": "approved",
        "product_spec_status": "finalized",
        "plan_status": "approved",
        "plan_approval_status": "approved",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "iteration_metrics": None,
        "retry_history": [],
        "routing_disagreements": [],
        "plan_version": 1,
        "release_version": "1.0.0",
        "last_evaluation": None,
        "active_change_request": None,
        "change_cycle": 0,
        "change_context": None,
    }


def _setup(tmp_path: Path) -> dict[str, object]:
    write_project_state_atomic(tmp_path / "project.yaml", _accepted_state())
    (tmp_path / "memory" / "plans").mkdir(parents=True)
    (tmp_path / "memory" / "plans" / "plan-001.md").write_text("# plan", encoding="utf-8")
    (tmp_path / "evaluation" / "reports").mkdir(parents=True)
    (tmp_path / "evaluation" / "reports" / "evaluation-001.md").write_text("# pass", encoding="utf-8")
    return create_change_request(
        tmp_path,
        raw_feedback="按另一张参考图修改 Dashboard",
        requested_changes=["调整 Dashboard 布局"],
        source_type="external_feedback",
        source_description="用户变更请求",
    )


def test_reference_binding_is_cr_scoped_and_history_is_append_only(tmp_path: Path) -> None:
    created = _setup(tmp_path)
    cr_id = str(created["change_request_id"])
    first = register_reference_change_request(
        tmp_path,
        cr_id,
        reference_ids=["REF-001", "REF-002"],
        synthesis_ref=f"change_requests/{cr_id}/references/synthesis-001.yaml",
        synthesis_version=1,
        approved_change_scope=["layout", "visual_style"],
        change_scope_approval_ref="approval-001",
        baseline_manifest=f"change_requests/{cr_id}/baseline/manifest.yaml",
    )
    second = register_reference_change_request(
        tmp_path,
        cr_id,
        reference_ids=["REF-003"],
        synthesis_ref=f"change_requests/{cr_id}/references/synthesis-002.yaml",
        synthesis_version=2,
        approved_change_scope=["navigation"],
        change_scope_approval_ref="approval-002",
        baseline_manifest=f"change_requests/{cr_id}/baseline/manifest.yaml",
    )
    assert first["binding_id"] == "CRREF-0001"
    assert second["supersedes"] == "CRREF-0001"
    history = list_reference_change_bindings(tmp_path, cr_id)
    assert len(history) == 2
    assert history[0]["reference_ids"] == ["REF-001", "REF-002"]
    assert history[0]["status"] == "ACTIVE"

    revoked = revoke_reference_change_binding(tmp_path, cr_id, reason="用户撤销视觉范围")
    assert revoked["status"] == "REVOKED"
    assert revoked["supersedes"] == "CRREF-0002"
    assert len(list_reference_change_bindings(tmp_path, cr_id)) == 3
    assert (tmp_path / "change_requests" / cr_id / "references" / "reference-binding-0001.yaml").is_file()


def test_reference_binding_cannot_initialize_new_project_or_cross_project(tmp_path: Path) -> None:
    write_project_state_atomic(tmp_path / "project.yaml", _accepted_state())
    with pytest.raises(Exception) as exc:
        register_reference_change_request(
            tmp_path,
            "CR-0001",
            reference_ids=["REF-001"],
            synthesis_ref="change_requests/CR-0001/references/synthesis-001.yaml",
            synthesis_version=1,
            approved_change_scope=["layout"],
            change_scope_approval_ref="approval-001",
            baseline_manifest="change_requests/CR-0001/baseline/manifest.yaml",
        )
    assert "REFERENCE_CR_REQUIRES_ACTIVE_CHANGE_REQUEST" in str(exc.value)
