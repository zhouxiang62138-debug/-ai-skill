"""R6 Evaluator Reference Conformance T01-T30 定向测试。"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from runtime.context.builder import ContextBuilder
from runtime.attestation import validate_verifier_results
from runtime.execution.path_policy import ExecutionPathPolicy
from runtime.verifiers import RuntimeVerifierRegistry
from scripts.evaluation_evidence import evaluate_gates, validate_evidence_manifest
from scripts.evaluation_protocol import validate_issue_package
from scripts.project_state import ProjectStateError
from scripts.reference_conformance import (
    build_reference_conformance_plan,
    reference_conformance_issues,
    validate_reference_conformance_section,
)
from scripts.reference_contract import build_approved_reference_contract


def _synthesis(two: bool = False) -> dict:
    return {
        "schema_version": 1,
        "synthesis_id": "REFSYN-001",
        "source_references": ["REF-001", "REF-002"] if two else ["REF-001"],
        "decisions": {
            "adopt": [
                {
                    "decision_id": "REFDEC-001",
                    "domain": "layout",
                    "source_findings": ["REFFND-001"],
                    "user_scope_status": "include",
                },
                *(
                    [
                        {
                            "decision_id": "REFDEC-002",
                            "domain": "navigation",
                            "source_findings": ["REFFND-002"],
                            "user_scope_status": "include",
                        }
                    ]
                    if two
                    else []
                ),
            ],
            "adapt": [],
            "avoid": [],
        },
    }


def _contract(*, two: bool = False, exclusions: list[str] | None = None) -> dict:
    synthesis = _synthesis(two)
    requirements = {"references": [], "explicit_exclusions": exclusions or []}
    spec = "# PS-001\nAC-001\nREFDEC-001"
    plan = "# TASK-001\nREFDEC-001"
    if two:
        spec += "\nAC-002\nREFDEC-002"
        plan += "\nTASK-002\nREFDEC-002"
    return build_approved_reference_contract(
        synthesis,
        synthesis_ref="memory/references/synthesis-001.yaml",
        active_requirements_ref="memory/requirements/requirements_v001.yaml",
        product_spec_ref="memory/specifications/product_spec_v001.md",
        approved_plan_ref="memory/plans/plan-001.md",
        product_approval_ref="memory/decisions/product-approval-001.md",
        plan_approval_ref="memory/decisions/plan-approval-001.md",
        product_spec_text=spec,
        approved_plan_text=plan,
        requirements=requirements,
        state={"requirements_version": 1, "product_spec_version": 1, "plan_version": 1},
    )


def _manifest(*, evidence_type: str = "static_check", owned: bool = True) -> dict:
    return {
        "evaluation_id": "evaluation-001",
        "commands": [{"command_id": "CMD-001"}],
        "reference_evidence": [
            {
                "evidence_id": "REF-EV-001",
                "evidence_type": evidence_type,
                "source": "evaluator",
                "evaluator_owned": owned,
                "evidence_refs": ["CMD-001"],
            }
        ],
    }


def _result(contract: dict, *, result: str = "PASS", evidence_id: str = "REF-EV-001", spec: str = "# PS-001\nAC-001\nREFDEC-001", plan: str = "# TASK-001\nREFDEC-001", capability: str | None = None) -> dict:
    binding = contract["reference_bindings"][0]
    conformance_type = "structural"
    if "visual" in spec.casefold():
        conformance_type = "visual"
    elif "behavior" in spec.casefold():
        conformance_type = "behavioral"
    return {
        "contract_id": contract["contract_id"],
        "contract_hash": contract["contract_hash"],
        "result": result,
        "binding_results": [
            {
                "reference_decision_id": binding["reference_decision_id"],
                "conformance_type": conformance_type,
                "result": result,
                "capability": capability or ({"visual": "unavailable"}.get(conformance_type, "available")),
                "acceptance_refs": list(binding["acceptance_refs"]),
                "plan_refs": list(binding["plan_refs"]),
                "evidence_refs": [evidence_id],
                "expected": "批准绑定应被实现",
                "observed": "实现证据已记录",
            }
        ],
    }


def test_T01_no_contract_is_not_applicable() -> None:
    errors, gate = validate_reference_conformance_section(None, None, _manifest())
    assert errors == []
    assert gate["result"] == "NOT_APPLICABLE"


def test_T02_plan_is_created_for_active_contract() -> None:
    plan = build_reference_conformance_plan(_contract())
    assert plan["gate_id"] == "GATE-REFERENCE-CONFORMANCE"
    assert plan["bindings"][0]["reference_decision_id"] == "REFDEC-001"


def test_T03_contract_is_the_only_reference_authority() -> None:
    plan = build_reference_conformance_plan(_contract())
    assert "raw_reference" not in str(plan)
    assert "raw_html" not in str(plan)


def test_T04_structural_binding_passes_with_independent_static_evidence() -> None:
    contract = _contract()
    errors, gate = validate_reference_conformance_section(
        _result(contract), contract, _manifest()
    )
    assert errors == []
    assert gate["result"] == "PASS"


def test_T05_behavioral_binding_uses_approved_text_type() -> None:
    contract = _contract()
    spec = "# PS-001\nAC-001 behavior click navigation\nREFDEC-001"
    result = _result(contract, spec=spec)
    result["binding_results"][0]["conformance_type"] = "behavioral"
    result["binding_results"][0]["capability"] = "available"
    errors, gate = validate_reference_conformance_section(
        result, contract, _manifest(evidence_type="browser_step"), approved_spec_text=spec
    )
    assert errors == []
    assert gate["result"] == "PASS"


def test_T06_visual_without_provider_is_blocked() -> None:
    contract = _contract()
    spec = "# PS-001\nAC-001 visual screenshot\nREFDEC-001"
    result = _result(contract, result="BLOCKED", spec=spec)
    errors, gate = validate_reference_conformance_section(
        result,
        contract,
        _manifest(evidence_type="screenshot"),
        approved_spec_text=spec,
    )
    assert errors == []
    assert gate["result"] == "BLOCKED"


def test_T07_visual_pass_claim_is_rejected() -> None:
    contract = _contract()
    spec = "# PS-001\nAC-001 visual screenshot\nREFDEC-001"
    result = _result(contract, spec=spec)
    errors, gate = validate_reference_conformance_section(
        result,
        contract,
        _manifest(evidence_type="screenshot"),
        approved_spec_text=spec,
    )
    assert gate is None
    assert any("REFERENCE_CAPABILITY_BLOCKED" in error for error in errors)


def test_T08_binding_fail_is_not_converted_to_pass() -> None:
    contract = _contract()
    result = _result(contract, result="FAIL")
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert errors == []
    assert gate["result"] == "FAIL"


def test_T09_unverified_binding_fails_gate() -> None:
    contract = _contract()
    result = _result(contract, result="UNVERIFIED")
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert errors == []
    assert gate["result"] == "FAIL"


def test_T10_multiple_bindings_are_independent() -> None:
    contract = _contract(two=True)
    result = _result(contract)
    second = dict(result["binding_results"][0])
    second["reference_decision_id"] = "REFDEC-002"
    second["acceptance_refs"] = contract["reference_bindings"][1]["acceptance_refs"]
    second["plan_refs"] = contract["reference_bindings"][1]["plan_refs"]
    result["binding_results"].append(second)
    manifest = _manifest()
    manifest["reference_evidence"].append({**manifest["reference_evidence"][0], "evidence_id": "REF-EV-002"})
    result["binding_results"][1]["evidence_refs"] = ["REF-EV-002"]
    errors, gate = validate_reference_conformance_section(result, contract, manifest)
    assert errors == []
    assert len(gate["binding_results"]) == 2


def test_T11_missing_binding_is_evidence_missing() -> None:
    contract = _contract(two=True)
    errors, gate = validate_reference_conformance_section(_result(contract), contract, _manifest())
    assert gate is None
    assert any("REFDEC-002" in error for error in errors)


def test_T12_contract_hash_prevents_stale_evaluation() -> None:
    contract = _contract()
    result = _result(contract)
    result["contract_hash"] = "0" * 64
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert gate is None
    assert any("REFERENCE_STALE_BINDING" in error for error in errors)


def test_T13_acceptance_trace_is_exact() -> None:
    contract = _contract()
    result = _result(contract)
    result["binding_results"][0]["acceptance_refs"] = ["AC-999"]
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert gate is None
    assert any("acceptance_refs" in error for error in errors)


def test_T14_plan_trace_is_exact() -> None:
    contract = _contract()
    result = _result(contract)
    result["binding_results"][0]["plan_refs"] = ["TASK-999"]
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert gate is None
    assert any("plan_refs" in error for error in errors)


def test_T15_unknown_reference_evidence_is_rejected() -> None:
    contract = _contract()
    result = _result(contract, evidence_id="REF-EV-999")
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert gate is None
    assert any("独立证据" in error for error in errors)


def test_T16_required_evidence_type_is_enforced() -> None:
    contract = _contract()
    result = _result(contract)
    errors, gate = validate_reference_conformance_section(
        result, contract, _manifest(evidence_type="screenshot")
    )
    assert gate is None
    assert any("REFERENCE_EVIDENCE_MISSING" in error for error in errors)


def test_T17_generator_handoff_alone_is_not_evaluator_evidence() -> None:
    contract = _contract()
    manifest = _manifest()
    manifest["reference_evidence"][0]["source"] = "generator_handoff"
    errors, gate = validate_reference_conformance_section(_result(contract), contract, manifest)
    assert gate is None
    assert any("source" in error for error in errors)


def test_T18_evidence_must_be_evaluator_owned() -> None:
    contract = _contract()
    errors, gate = validate_reference_conformance_section(
        _result(contract), contract, _manifest(owned=False)
    )
    assert gate is None
    assert any("evaluator_owned" in error for error in errors)


def test_T19_raw_reference_path_cannot_be_evidence_id() -> None:
    contract = _contract()
    result = _result(contract, evidence_id="memory/references/raw.html")
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert gate is None
    assert any("独立证据" in error for error in errors)


def test_T20_exclusion_violation_is_preserved() -> None:
    contract = _contract(exclusions=["animation"])
    result = _result(contract)
    result["binding_results"][0]["exclusion_checks"] = [{"exclusion_ref": "animation", "result": "FAIL", "expected": "不得加入 animation"}]
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert errors == []
    assert gate["result"] == "FAIL"


def test_T21_exclusion_environment_block_is_blocked() -> None:
    contract = _contract(exclusions=["animation"])
    result = _result(contract)
    result["binding_results"][0]["exclusion_checks"] = [{"exclusion_ref": "animation", "result": "BLOCKED"}]
    errors, gate = validate_reference_conformance_section(result, contract, _manifest())
    assert errors == []
    assert gate["result"] == "BLOCKED"


def test_T22_reference_failure_creates_generator_issue() -> None:
    issues = reference_conformance_issues(
        "evaluation-001",
        {
            "result": "FAIL",
            "binding_results": [{
                "reference_decision_id": "REFDEC-001",
                "result": "FAIL",
                "capability": "available",
                "evidence_refs": ["REF-EV-001"],
                "acceptance_refs": ["AC-001"],
            }],
        },
    )
    assert issues[0]["route_to"] == "GENERATOR"
    assert validate_issue_package({
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "project_id": "test_r6_reference_conformance",
        "result": "FAIL",
        "created_at": "2026-08-09T00:00:00+08:00",
        "current_iteration": 0,
        "return_to": "GENERATOR",
        "report_reference": "evaluation/reports/evaluation-001.md",
        "previous_evaluation": None,
        "summary": {"total_issues": 1, "blocking_issues": 0, "non_blocking_issues": 1},
        "issues": issues,
    }) == []


def test_T23_capability_issue_routes_to_blocked() -> None:
    issue = reference_conformance_issues("evaluation-001", {
        "result": "BLOCKED",
        "binding_results": [{
            "reference_decision_id": "REFDEC-001",
            "result": "BLOCKED",
            "capability": "unavailable",
            "evidence_refs": ["REF-EV-001"],
            "acceptance_refs": ["AC-001"],
        }],
    })[0]
    assert issue["category"] == "reference_capability_blocked"
    assert issue["route_to"] == "SYSTEM_OR_USER"


def test_T24_reference_gate_without_contract_is_na() -> None:
    gates = evaluate_gates(
        {},
        [{"id": "GATE-REFERENCE-CONFORMANCE", "required": False}],
    )
    assert gates[0]["result"] == "NOT_APPLICABLE"


def test_T25_reference_gate_active_passes_with_evidence() -> None:
    gates = evaluate_gates(
        {"GATE-REFERENCE-CONFORMANCE": {"result": "PASS", "evidence_refs": ["REF-EV-001"]}},
        [{"id": "GATE-REFERENCE-CONFORMANCE", "required": False}],
    )
    assert gates[0]["result"] == "PASS"


def test_T26_manifest_accepts_reference_na_gate() -> None:
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "created_at": "2026-08-09T00:00:00+08:00",
        "environment": {"os": "windows", "architecture": "x64", "python_version": "3.11", "working_directory": "code"},
        "commands": [], "artifacts": [], "checks": [],
        "gates": [{"gate_id": "GATE-REFERENCE-CONFORMANCE", "required": False, "result": "NOT_APPLICABLE", "evidence_refs": [], "reason": "no_approved_reference_contract"}],
    }
    assert validate_evidence_manifest(manifest) == []


def test_T27_manifest_registers_independent_reference_evidence() -> None:
    manifest = _manifest()
    assert validate_evidence_manifest({
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "created_at": "2026-08-09T00:00:00+08:00",
        "environment": {"os": "windows", "architecture": "x64", "python_version": "3.11", "working_directory": "code"},
        "commands": [], "artifacts": [], "checks": [], "gates": [],
        "reference_evidence": manifest["reference_evidence"],
    }) == ["reference_evidence[0].evidence_refs 存在未知证据 ID"]


def test_T28_evaluator_context_source_is_configured() -> None:
    text = Path("config/context.yaml").read_text(encoding="utf-8")
    assert "reference_conformance_subset" in text
    assert "Evaluator 仅消费批准 Reference Contract" in text


def test_T29_evaluator_additional_raw_reference_is_denied(tmp_path: Path) -> None:
    (tmp_path / "project.yaml").write_text("active_reference_synthesis: memory/references/synthesis.yaml\n", encoding="utf-8")
    builder = object.__new__(ContextBuilder)
    builder._context_policy = type("Policy", (), {"rules_for": lambda self, role: ()})()
    builder._path_policy = ExecutionPathPolicy()
    with pytest.raises(Exception, match="CONTEXT_GENERATOR_REFERENCE_SCOPE"):
        builder._collect_sources("evaluator", tmp_path, {"active_reference_synthesis": "active"}, ("memory/references/raw.html",))


def _write_runtime_project(root: Path) -> None:
    state = {
        "project_id": "test_r6_reference_conformance",
        "active_requirements": "memory/requirements/requirements_v001.yaml",
        "active_reference_synthesis": "memory/references/synthesis-001.yaml",
        "active_product_spec": "memory/specifications/product_spec_v001.md",
        "approved_plan": "memory/plans/plan-001.md",
        "product_approval_record": "memory/decisions/product-approval-001.md",
        "plan_approval_record": "memory/decisions/plan-approval-001.md",
        "requirements_version": 1,
        "product_spec_version": 1,
        "plan_version": 1,
    }
    (root / "project.yaml").write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    files = {
        state["active_requirements"]: "schema_version: 1\nexplicit_exclusions: []\n",
        state["active_reference_synthesis"]: yaml.safe_dump(_synthesis(), sort_keys=False),
        state["active_product_spec"]: "# PS-001\nAC-001\nREFDEC-001\n",
        state["approved_plan"]: "# TASK-001\nREFDEC-001\n",
        state["product_approval_record"]: "approved\n",
        state["plan_approval_record"]: "approved\n",
    }
    for reference, content in files.items():
        path = root / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_T30_runtime_verifier_closes_reference_gate(tmp_path: Path) -> None:
    _write_runtime_project(tmp_path)
    from runtime.reference_contract import build_reference_contract_for_project

    contract = build_reference_contract_for_project(tmp_path)
    assert contract is not None
    response = {
        "evidence_references": ["REF-EV-001"],
        "reference_conformance": _result(contract),
        "evidence_manifest": _manifest(),
    }
    result = RuntimeVerifierRegistry(tmp_path).verify("evidence_manifest", None, response)
    assert result["passed"] is True
    assert "reference_conformance_gate=PASS" in result["details"]
    safe, evidence_refs = validate_verifier_results({"evidence_manifest": result})
    assert safe["evidence_manifest"]["reference_gate"]["contract_hash"] == contract["contract_hash"]
    assert "evidence-manifest:evaluation-001" in evidence_refs
