import pytest

from runtime.errors import RuntimeValidationError
from runtime.policy import assert_field_ownership, assert_state_transition, load_field_ownership, load_runtime_routes
from runtime.runtime_config import load_runtime_config


def test_field_ownership_is_loaded_from_config() -> None:
    assert "active_plan" in load_field_ownership()["planner"]


def test_runtime_routes_are_loaded_from_workflow_config() -> None:
    routes = load_runtime_routes()
    assert routes["PLANNING"]["next_role"] == "planner"
    assert routes["WAITING_FOR_USER"]["next_role"] is None


def test_runtime_database_config_is_loaded() -> None:
    config = load_runtime_config()
    assert config["busy_timeout_ms"] == 5000
    assert config["payload_limit_bytes"] == 65536
    assert config["control_plane_root"].endswith(".ai-development-team/runtime")


def test_illegal_state_transition_fails() -> None:
    with pytest.raises(RuntimeValidationError, match="ILLEGAL_STATE_TRANSITION"):
        assert_state_transition("PLANNING", "ACCEPTED")


def test_valid_state_transition_passes() -> None:
    assert_state_transition("PLANNING", "WAITING_FOR_PRODUCT_REVIEW")


def test_role_cannot_modify_unowned_field() -> None:
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        assert_field_ownership("planner", {"current_iteration": 0}, {"current_iteration": 1})


def test_runtime_cannot_modify_business_field() -> None:
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        assert_field_ownership("runtime_orchestrator", {"plan_status": "draft"}, {"plan_status": "approved"})
