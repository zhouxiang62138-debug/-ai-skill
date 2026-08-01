import pytest

from runtime.errors import RuntimeValidationError
from runtime.policy import assert_field_ownership, load_field_ownership, load_runtime_routes


def test_field_ownership_is_loaded_from_config() -> None:
    assert "active_plan" in load_field_ownership()["planner"]


def test_runtime_routes_are_loaded_from_workflow_config() -> None:
    routes = load_runtime_routes()
    assert routes["PLANNING"]["next_role"] == "planner"
    assert routes["WAITING_FOR_USER"]["next_role"] is None


def test_role_cannot_modify_unowned_field() -> None:
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        assert_field_ownership("planner", {"current_iteration": 0}, {"current_iteration": 1})


def test_runtime_cannot_modify_business_field() -> None:
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        assert_field_ownership("runtime_orchestrator", {"plan_status": "draft"}, {"plan_status": "approved"})
