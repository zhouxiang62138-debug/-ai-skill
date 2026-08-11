"""Runtime-owned Role Verifier Registry。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import ProjectStateError, load_project_state

from .errors import RuntimeValidationError
from .evaluator_independence import validate_evaluator_evidence
from scripts.reference_contract import validate_approved_reference_contract
from scripts.reference_conformance import validate_reference_conformance_section


RUNTIME_FIELDS = frozenset({"next_role", "active_module", "runtime", "schema_version", "reference_contract", "approved_reference_contract"})
PROTECTED_ROOTS = frozenset({"project.yaml", "memory/plans", "memory/specifications", "memory/decisions"})


def _references(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return []
    return list(dict.fromkeys(value))


def _existing_project_refs(root: Path, refs: list[str]) -> list[str]:
    evidence: list[str] = []
    for reference in refs:
        path = (root / reference).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeValidationError("VERIFIER_REFERENCE_PATH_ESCAPE") from exc
        if not path.is_file():
            raise RuntimeValidationError("VERIFIER_EVIDENCE_MISSING:" + reference)
        evidence.append(reference)
    return evidence


def _contains_forbidden_conformance_claim(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden_conformance_claim(key)
            or _contains_forbidden_conformance_claim(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_conformance_claim(item) for item in value)
    if not isinstance(value, str):
        return False
    text = value.casefold()
    return any(
        marker in text and "pass" in text
        for marker in (
            "reference conformance",
            "visual similarity",
            "reference fidelity",
            "evaluation",
        )
    )


def _validate_reference_handoff(
    response: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> tuple[bool, str]:
    if _contains_forbidden_conformance_claim(response):
        return False, "HANDOFF_REFERENCE_CONFORMANCE_CLAIM_FORBIDDEN"
    contract = preflight.get("reference_contract")
    if contract is None:
        return True, ""
    bindings = contract.get("reference_bindings") if isinstance(contract, Mapping) else None
    if not isinstance(bindings, list) or not bindings:
        return False, "HANDOFF_REFERENCE_CONTRACT_INVALID"
    expected_by_id = {
        str(item.get("reference_decision_id")): item
        for item in bindings
        if isinstance(item, Mapping)
    }
    expected_ids = set(expected_by_id)
    reported = response.get("implemented_reference_bindings")
    if not isinstance(reported, list):
        return False, "HANDOFF_REFERENCE_BINDINGS_MISSING"
    reported_ids: set[str] = set()
    allowed_statuses = {"implemented", "not_implemented", "blocked", "deviation_detected"}
    for item in reported:
        if not isinstance(item, Mapping):
            return False, "HANDOFF_REFERENCE_BINDING_INVALID"
        decision_id = item.get("reference_decision_id")
        status = item.get("status")
        if not isinstance(decision_id, str) or decision_id in reported_ids:
            return False, "HANDOFF_REFERENCE_BINDING_ID_INVALID"
        if decision_id not in expected_ids or status not in allowed_statuses:
            return False, "HANDOFF_REFERENCE_BINDING_SCOPE_INVALID"
        expected_binding = expected_by_id[decision_id]
        reported_plan_refs = _references(item.get("plan_refs"))
        reported_acceptance_refs = _references(item.get("acceptance_refs"))
        if not reported_plan_refs or not reported_acceptance_refs:
            return False, "HANDOFF_REFERENCE_TRACE_MISSING"
        if (
            set(reported_plan_refs) != set(_references(expected_binding.get("plan_refs")))
            or set(reported_acceptance_refs)
            != set(_references(expected_binding.get("acceptance_refs")))
        ):
            return False, "HANDOFF_REFERENCE_TRACE_MISMATCH"
        reported_ids.add(decision_id)
    if reported_ids != expected_ids:
        return False, "HANDOFF_REFERENCE_BINDING_SET_MISMATCH"
    return True, ""


def _active_reference_contract(root: Path) -> tuple[dict[str, Any] | None, str, str]:
    """读取当前批准链生成的 Contract；不读取原始 Reference 内容。"""

    project_yaml = root / "project.yaml"
    if not project_yaml.is_file():
        return None, "", ""
    state = load_project_state(project_yaml)
    if not state.get("active_reference_synthesis"):
        return None, "", ""
    from .reference_contract import build_reference_contract_for_project

    contract = build_reference_contract_for_project(root)
    if contract is None:
        return None, "", ""
    spec_ref = state.get("active_product_spec")
    plan_ref = state.get("approved_plan")
    if not isinstance(spec_ref, str) or not isinstance(plan_ref, str):
        raise ProjectStateError("REFERENCE_CONTRACT_APPROVED_SOURCE_INVALID")
    spec_path = (root / spec_ref).resolve()
    plan_path = (root / plan_ref).resolve()
    for path in (spec_path, plan_path):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_PATH_ESCAPE") from exc
        if not path.is_file():
            raise ProjectStateError("REFERENCE_CONTRACT_APPROVED_SOURCE_MISSING")
    return contract, spec_path.read_text(encoding="utf-8"), plan_path.read_text(encoding="utf-8")


def _verify_reference_conformance(
    root: Path,
    response: Mapping[str, Any],
) -> dict[str, Any] | None:
    """在既有 Evidence Manifest Verifier 中执行 Reference Conformance Gate。"""

    contract, spec_text, plan_text = _active_reference_contract(root)
    section = response.get("reference_conformance")
    manifest = response.get("evidence_manifest")
    if manifest is None:
        manifest = response.get("evaluation_evidence_manifest")
    if manifest is None:
        manifest = {}
    if not isinstance(manifest, Mapping):
        return {"passed": False, "evidence_refs": [], "details": "REFERENCE_EVIDENCE_MANIFEST_INVALID"}
    if contract is None:
        errors, gate = validate_reference_conformance_section(section, None, manifest)
        if errors:
            return {"passed": False, "evidence_refs": [], "details": errors[0]}
        return {
            "passed": True,
            "evidence_refs": ["runtime:reference-conformance:not-applicable"],
            "details": "reference_conformance_not_applicable",
            "reference_gate": gate,
        }
    errors, gate = validate_reference_conformance_section(
        section,
        contract,
        manifest,
        approved_spec_text=spec_text,
        approved_plan_text=plan_text,
    )
    if errors:
        return {"passed": False, "evidence_refs": [], "details": errors[0]}
    assert gate is not None
    refs = [
        "runtime:reference-conformance:" + str(contract["contract_hash"]),
        "evidence-manifest:" + str(manifest.get("evaluation_id", "unknown")),
        *[
            "reference-binding:" + str(item["reference_decision_id"]) + ":" + str(item["result"])
            for item in gate["binding_results"]
        ],
    ]
    return {
        "passed": True,
        "evidence_refs": refs,
        "details": "reference_conformance_gate=" + str(gate["result"]),
        "reference_gate": gate,
    }


class RuntimeVerifierRegistry:
    """由 Runtime 固定分派的 Verifier；正式入口不接受任意 lambda。"""

    def __init__(self, project_root: str | Path, session_store: Any | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.session_store = session_store

    def verify_transition(
        self,
        role: str,
        source_status: str,
        target_status: str,
        changed_fields: Mapping[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        """验证确定性 Module transition 的当前状态、revision 和文件证据。"""

        state = load_project_state(self.root / "project.yaml")
        runtime = state.get("runtime")
        if role not in {"planner", "generator", "evaluator"}:
            return {"passed": False, "evidence_refs": [], "details": "ROLE_NOT_ALLOWED"}
        if state.get("status") != source_status or not isinstance(runtime, Mapping) or int(runtime.get("revision", -1)) != expected_revision:
            return {"passed": False, "evidence_refs": [], "details": "TRANSITION_SOURCE_OR_REVISION_MISMATCH"}
        if not isinstance(changed_fields, Mapping) or any(key in changed_fields for key in {"runtime", "schema_version"}):
            return {"passed": False, "evidence_refs": [], "details": "TRANSITION_RUNTIME_FIELD_FORBIDDEN"}
        refs: list[str] = ["runtime:transition-source"]
        for key, value in changed_fields.items():
            if not isinstance(key, str) or not key.strip():
                return {"passed": False, "evidence_refs": [], "details": "TRANSITION_FIELD_INVALID"}
            if isinstance(value, str) and ("/" in value or "\\" in value) and not value.startswith("http"):
                normalized = value.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    return {"passed": False, "evidence_refs": [], "details": "TRANSITION_REFERENCE_ESCAPE"}
                candidate = (self.root / value).resolve()
                try:
                    candidate.relative_to(self.root)
                except ValueError:
                    return {"passed": False, "evidence_refs": [], "details": "TRANSITION_REFERENCE_ESCAPE"}
                if not candidate.is_file():
                    return {"passed": False, "evidence_refs": [], "details": "TRANSITION_REFERENCE_MISSING:" + value}
                refs.append(value)
        return {"passed": True, "evidence_refs": refs, "details": "runtime_transition_verifier"}

    def verify(
        self,
        step: str,
        request: Any,
        response: Mapping[str, Any],
        *,
        preflight: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行一个受控 Verifier，返回结构化结果与证据引用。"""

        if not isinstance(response, Mapping):
            return {"passed": False, "evidence_refs": [], "details": "MODEL_OUTPUT_INVALID"}
        if any(field in response for field in RUNTIME_FIELDS):
            return {"passed": False, "evidence_refs": [], "details": "MODEL_RUNTIME_FIELD_FORBIDDEN"}
        if step == "contract_preflight":
            if preflight is None or preflight.get("passed") is not True:
                return {"passed": False, "evidence_refs": [], "details": "CONTRACT_PREFLIGHT_EVIDENCE_MISSING"}
            contract = preflight.get("reference_contract")
            evidence = ["runtime:contract_preflight"]
            if contract is not None:
                try:
                    validate_approved_reference_contract(contract)
                except ProjectStateError as exc:
                    return {"passed": False, "evidence_refs": [], "details": str(exc)}
                evidence.append("runtime:approved-reference-contract:" + str(contract["contract_hash"]))
            return {"passed": True, "evidence_refs": evidence, "details": "runtime_execution"}
        if step in {"source_chain", "proposal_or_plan"}:
            state = load_project_state(self.root / "project.yaml")
            refs = [
                state.get("active_requirements"),
                state.get("approved_proposal"),
                state.get("active_product_spec"),
                state.get("approved_plan"),
                state.get("product_approval_record"),
                state.get("plan_approval_record"),
            ]
            refs = [item for item in refs if isinstance(item, str) and item]
            if step == "proposal_or_plan":
                refs = [item for item in refs if "proposal" in item or "plan" in item or "specification" in item]
            existing = _existing_project_refs(self.root, refs)
            if step == "source_chain" and not existing:
                return {"passed": False, "evidence_refs": [], "details": "APPROVED_SOURCE_CHAIN_MISSING"}
            return {"passed": True, "evidence_refs": existing, "details": "runtime_source_chain"}
        if step == "handoff":
            if preflight is not None:
                valid, details = _validate_reference_handoff(response, preflight)
                if not valid:
                    return {"passed": False, "evidence_refs": [], "details": details}
            refs = _references(response.get("handoff_references"))
            try:
                existing = _existing_project_refs(self.root, refs)
            except RuntimeValidationError as exc:
                return {"passed": False, "evidence_refs": [], "details": str(exc)}
            return {
                "passed": bool(existing),
                "evidence_refs": existing,
                "details": "handoff_evidence" if existing else "HANDOFF_EVIDENCE_MISSING",
            }
        if step == "tests":
            evidence = response.get("execution_evidence")
            if not isinstance(evidence, list) or not evidence:
                return {"passed": False, "evidence_refs": [], "details": "EXECUTION_EVIDENCE_MISSING"}
            refs: list[str] = []
            for item in evidence:
                if not isinstance(item, Mapping) or item.get("exit_code") != 0:
                    return {"passed": False, "evidence_refs": [], "details": "EXECUTION_RESULT_INVALID"}
                ref = item.get("evidence_ref")
                if not isinstance(ref, str) or not ref:
                    return {"passed": False, "evidence_refs": [], "details": "EXECUTION_EVIDENCE_REF_MISSING"}
                refs.append(ref)
            return {"passed": True, "evidence_refs": refs, "details": "execution_broker_result"}
        if step == "evaluator_independence":
            if getattr(request, "role", None) != "evaluator":
                return {
                    "passed": False,
                    "evidence_refs": [],
                    "details": "EVALUATOR_INDEPENDENCE_ROLE_INVALID",
                }
            return validate_evaluator_evidence(
                response,
                request,
                self.root,
                session_store=self.session_store,
            )
        if step == "implementation":
            paths = _references(response.get("implementation_paths"))
            if not paths:
                return {"passed": False, "evidence_refs": [], "details": "IMPLEMENTATION_EVIDENCE_MISSING"}
            for reference in paths:
                normalized = reference.replace("\\", "/")
                if normalized == "code" or normalized.startswith("code/") is False:
                    return {"passed": False, "evidence_refs": [], "details": "IMPLEMENTATION_PATH_INVALID"}
            return {"passed": True, "evidence_refs": paths, "details": "role_policy_scope"}
        if step in {"browser_scenarios", "feature_completeness", "build_test_regression", "evidence_manifest", "issue_package", "candidate", "evaluation_transaction", "evidence", "regression"}:
            refs = _references(response.get("evidence_references"))
            if not refs:
                return {"passed": False, "evidence_refs": [], "details": "RUNTIME_EVIDENCE_MISSING"}
            if step == "evidence_manifest":
                try:
                    reference_verdict = _verify_reference_conformance(self.root, response)
                except (ProjectStateError, RuntimeValidationError, OSError) as exc:
                    return {"passed": False, "evidence_refs": [], "details": str(exc)}
                if reference_verdict is not None:
                    if reference_verdict.get("passed") is not True:
                        return reference_verdict
                    return {
                        "passed": True,
                        "evidence_refs": refs + list(reference_verdict.get("evidence_refs") or []),
                        "details": reference_verdict.get("details", "runtime_evidence_reference"),
                        "reference_gate": reference_verdict.get("reference_gate"),
                    }
            return {"passed": True, "evidence_refs": refs, "details": "runtime_evidence_reference"}
        return {"passed": False, "evidence_refs": [], "details": "VERIFIER_NOT_REGISTERED:" + step}


__all__ = ["RuntimeVerifierRegistry"]
