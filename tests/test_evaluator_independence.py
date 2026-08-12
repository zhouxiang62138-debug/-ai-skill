"""E1 Evaluator Independence Hardening 的定向测试。"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.evaluator_independence import (
    EvaluatorIndependencePolicy,
    code_snapshot_hash,
    validate_evaluator_evidence,
)
from runtime.errors import RuntimeValidationError
from runtime.execution.path_policy import ExecutionPathPolicy
from runtime.session_store import SessionStore
from runtime.verifiers import RuntimeVerifierRegistry
from tests.test_formal_context_builder import _prepare_project

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluation_evidence import validate_evaluator_independence_manifest  # noqa: E402


def _evaluator_context(tmp_path: Path):
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "EVALUATING", "evaluator"
    )
    hidden = root / "memory/handoffs/responses/response-001.md"
    hidden.write_text("GENERATOR_PRIVATE_SENTINEL_9281\nall tests passed\n", encoding="utf-8")
    context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, run_id, "evaluator")
    )
    request = SimpleNamespace(
        role="evaluator",
        invocation_id="model-invocation-e1",
        context=context.manifest,
        context_manifest_id=context.context_id,
        source_revision=context.project_revision,
        code_snapshot_hash=code_snapshot_hash(root),
    )
    return store, session_id, run_id, root, context, request


def _evidence(request: SimpleNamespace, *, provenance: str = "EVALUATOR_REPRODUCED") -> dict:
    return {
        "acceptance_criterion_id": "AC-001",
        "required": True,
        "result": "PASS",
        "provenance": provenance,
        "tool_call_id": "tool-call-e1",
        "attempt_id": "attempt-e1",
        "command": ["python", "-m", "pytest"],
        "environment": {"python_version": "3.11"},
        "result_hash": "a" * 64,
        "timestamp": "2026-08-11T10:00:00+00:00",
        "project_revision": request.source_revision,
        "code_snapshot_hash": request.code_snapshot_hash,
    }


def _response(request: SimpleNamespace, **overrides) -> dict:
    independent = {
        "fresh_invocation": True,
        "invocation_id": request.invocation_id,
        "evaluator_invocation_id": request.invocation_id,
        "context_manifest_id": request.context_manifest_id,
        "project_revision": request.source_revision,
        "code_snapshot_hash": request.code_snapshot_hash,
        "generator_claims_are_evidence": False,
        "reproduction": {
            "build": "PASS",
            "required_tests": "PASS",
            "browser_required_scenarios": "PASS",
            "regression": "PASS",
        },
        "blocking_issues": [],
        "critical_issues": [],
        "protected_artifacts_unchanged": True,
        "prior_blocking_issues_resolved": True,
        "runtime_verifiers_passed": True,
        "required_acceptance_criteria": ["AC-001"],
        "evidence": [_evidence(request)],
    }
    independent.update(overrides)
    return {"evaluator_independence": independent}


def test_evaluator_context_excludes_generator_response_and_marks_blind_review(tmp_path: Path) -> None:
    store, session_id, _, _, context, _ = _evaluator_context(tmp_path)
    assert context.context_type == "EVALUATOR_INDEPENDENT"
    assert "generator_self_assessment" in context.excluded_sources
    assert all("GENERATOR_PRIVATE_SENTINEL_9281" not in str(source.to_dict()) for source in context.sources)
    assert all("responses/" not in source.reference for source in context.sources)
    durable = store.get_context_manifest(session_id, context.context_id)
    assert durable["context_type"] == "EVALUATOR_INDEPENDENT"
    assert "generator_self_assessment" in durable["excluded_sources"]


def test_model_invocation_metadata_binds_revision_and_context(tmp_path: Path) -> None:
    store, session_id, run_id, _, context, _ = _evaluator_context(tmp_path)
    invocation = store.create_model_invocation(
        session_id,
        run_id,
        "evaluator",
        context.context_id,
        idempotency_key="e1-invocation",
        source_revision=context.project_revision,
        model_id="same-model-is-allowed",
        capability_profile="FULL",
        fresh_context_required=True,
    )
    assert invocation["context_manifest_id"] == context.context_id
    assert invocation["source_revision"] == context.project_revision
    assert invocation["fresh_context_required"] == 1
    assert invocation["model_id"] == "same-model-is-allowed"


def test_evaluator_cannot_start_while_previous_invocation_is_active(tmp_path: Path) -> None:
    store, session_id, run_id, _, context, _ = _evaluator_context(tmp_path)
    store.create_model_invocation(
        session_id,
        run_id,
        "evaluator",
        context.context_id,
        idempotency_key="e1-active",
        source_revision=context.project_revision,
    )
    next_run = store.create_role_run(session_id, "worker-context", "evaluator")
    next_context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, next_run, "evaluator")
    )
    with pytest.raises(RuntimeValidationError, match="EVALUATOR_FRESH_INVOCATION_REQUIRED"):
        store.create_model_invocation(
            session_id,
            next_run,
            "evaluator",
            next_context.context_id,
            idempotency_key="e1-second-active",
            source_revision=next_context.project_revision,
        )


def test_generator_claim_alone_is_not_independent_evidence(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    response = _response(request)
    response["evaluator_independence"]["evidence"][0]["provenance"] = "GENERATOR_PROVIDED"
    result = validate_evaluator_evidence(response, request, root)
    assert result["passed"] is False
    assert result["details"] == "EVIDENCE_PROVENANCE_INSUFFICIENT"


def test_evaluator_reproduction_can_pass_deterministic_gate(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    result = validate_evaluator_evidence(_response(request), request, root)
    assert result["passed"] is True
    assert result["details"] == "e1_deterministic_pass_gate"


def test_session_store_provenance_must_match_tool_attempt(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    evidence = _response(request)["evaluator_independence"]["evidence"][0]
    result_hash = evidence["result_hash"]

    class StoreStub:
        def get_tool_call(self, session_id: str, tool_call_id: str) -> dict:
            return {
                "status": "SUCCEEDED",
                "result_reference": "tool-results/tool-call-e1/result.json",
                "result_hash": result_hash,
            }

        def get_tool_attempt(self, session_id: str, attempt_id: str) -> dict:
            return {
                "status": "SUCCEEDED",
                "tool_call_id": evidence["tool_call_id"],
                "result_hash": result_hash,
                "code_snapshot_hash": request.code_snapshot_hash,
            }

        def read_tool_result(self, reference: str, expected_hash: str) -> dict:
            return {"exit_code": 0}

    assert validate_evaluator_evidence(
        _response(request), request, root, session_store=StoreStub()
    )["passed"] is True

    tampered = _response(request)
    tampered["evaluator_independence"]["evidence"][0]["result_hash"] = "c" * 64
    result = validate_evaluator_evidence(
        tampered, request, root, session_store=StoreStub()
    )
    assert result["passed"] is False
    assert result["details"] == "EVIDENCE_PROVENANCE_INSUFFICIENT"


def test_failed_browser_reproduction_denies_model_pass(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    response = _response(request)
    response["evaluator_independence"]["reproduction"]["browser_required_scenarios"] = "FAIL"
    result = validate_evaluator_evidence(response, request, root)
    assert result["passed"] is False
    assert result["details"] == "EVALUATOR_REPRODUCTION_REQUIRED"


def test_old_revision_evidence_cannot_support_current_pass(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    response = _response(request)
    response["evaluator_independence"]["evidence"][0]["project_revision"] -= 1
    result = validate_evaluator_evidence(response, request, root)
    assert result["passed"] is False
    assert result["details"] == "EVIDENCE_REVISION_MISMATCH"


def test_code_snapshot_mismatch_denies_pass(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    response = _response(request)
    response["evaluator_independence"]["code_snapshot_hash"] = "b" * 64
    result = validate_evaluator_evidence(response, request, root)
    assert result["passed"] is False
    assert result["details"] == "EVIDENCE_REVISION_MISMATCH"


def test_runtime_verifier_rejects_model_pass_without_e1_payload(tmp_path: Path) -> None:
    _, _, _, root, _, request = _evaluator_context(tmp_path)
    result = RuntimeVerifierRegistry(root).verify(
        "evaluator_independence", request, {"completed_steps": ["evaluator_independence"]}
    )
    assert result["passed"] is False
    assert result["details"] == "DETERMINISTIC_PASS_GATE_FAILED"


def test_evaluator_path_policy_denies_code_and_evaluation_rules(tmp_path: Path) -> None:
    policy = ExecutionPathPolicy()
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_PROHIBITED"):
        policy.assert_path("evaluator", tmp_path, "code/app.py", operation="write")
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_PROHIBITED"):
        policy.assert_path(
            "evaluator", tmp_path, "config/evaluation_rules/default.yaml", operation="write"
        )


def test_evidence_manifest_provenance_is_revision_bound(tmp_path: Path) -> None:
    _, _, _, _, _, request = _evaluator_context(tmp_path)
    section = _response(request)["evaluator_independence"]
    manifest = {
        "evaluator_independence": {
            **section,
            "required_acceptance_criteria": ["AC-001"],
        }
    }
    assert validate_evaluator_independence_manifest(manifest) == []
    manifest["evaluator_independence"]["evidence"][0]["project_revision"] -= 1
    assert any("project_revision" in item for item in validate_evaluator_independence_manifest(manifest))


def test_policy_is_explicitly_named_e1() -> None:
    policy = EvaluatorIndependencePolicy.load()
    assert policy.upgrade_id == "E1"
    assert policy.fresh_invocation_required is True
    assert policy.generator_claims_are_evidence is False
