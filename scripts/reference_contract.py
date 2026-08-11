"""Generator 可消费的 Approved Reference Contract 确定性构建与校验。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .project_state import ProjectStateError


_REFDEC = re.compile(r"\bREFDEC-[0-9]{3,4}\b")
_REF = re.compile(r"\bREF-[0-9]{3,4}\b")
_REFFND = re.compile(r"\bREFFND-[0-9]{3,4}\b")
_REQ = re.compile(r"\bREQ-[A-Za-z0-9_-]+\b")
_PS = re.compile(r"\bPS-[A-Za-z0-9_-]+\b")
_TASK = re.compile(r"\bTASK-[A-Za-z0-9_-]+\b")
_AC = re.compile(r"\bAC-[A-Za-z0-9_-]+\b")
_SECRET = re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|secret|private[_-]?key)\s*[:=]")
_RAW_REFERENCE_KEYS = frozenset({"raw_reference", "raw_url", "raw_html", "image_data", "screenshot"})


def canonical_reference_contract(value: Mapping[str, Any]) -> str:
    """返回 Contract 的稳定 JSON；不保存原始 Reference 内容。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reference_contract_context_hash(contract: Mapping[str, Any]) -> str:
    """计算 Context 中 Contract 摘要的内容 hash。"""

    return hashlib.sha256(canonical_reference_contract(contract).encode("utf-8")).hexdigest()


def _ids(pattern: re.Pattern[str], *values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in pattern.findall(value or ""):
            if item not in result:
                result.append(item)
    return result


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _exclusions(requirements: Mapping[str, Any] | None, synthesis: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    requirements = requirements or {}
    for key in ("explicit_exclusions", "excluded_domains", "exclusions"):
        raw = requirements.get(key)
        if isinstance(raw, list):
            values.update(str(item) for item in raw if str(item).strip())
    references = requirements.get("references", [])
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, Mapping):
                raw = reference.get("explicit_exclusions", [])
                if isinstance(raw, list):
                    values.update(str(item) for item in raw if str(item).strip())
    raw_synthesis = synthesis.get("explicit_exclusions", [])
    if isinstance(raw_synthesis, list):
        values.update(str(item) for item in raw_synthesis if str(item).strip())
    return sorted(values)


def _decision_map(synthesis: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    decisions = synthesis.get("decisions")
    if not isinstance(decisions, Mapping):
        return result
    for bucket in ("adopt", "adapt", "avoid"):
        raw = decisions.get(bucket, [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, Mapping) and isinstance(item.get("decision_id"), str):
                result[str(item["decision_id"])] = (bucket, item)
    return result


def _state_versions(state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    return {
        "requirements_version": state.get("requirements_version"),
        "product_proposal_version": state.get("proposal_version"),
        "product_spec_version": state.get("product_spec_version"),
        "plan_version": state.get("plan_version"),
        "design_selection_version": state.get("design_feedback_round"),
        "product_approval_record": state.get("product_approval_record"),
        "plan_approval_record": state.get("plan_approval_record"),
    }


def build_approved_reference_contract(
    synthesis: Mapping[str, Any] | None,
    *,
    synthesis_ref: str,
    active_requirements_ref: str | None,
    product_spec_ref: str,
    approved_plan_ref: str,
    product_approval_ref: str | None,
    plan_approval_ref: str,
    product_spec_text: str,
    approved_plan_text: str,
    requirements: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """从已批准的 Spec/Plan 派生 Contract；未采用 Reference 时返回 None。"""

    if synthesis is None:
        return None
    if not isinstance(synthesis, Mapping):
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_INVALID")
    if synthesis.get("status") in {"superseded", "revoked", "withdrawn"}:
        raise ProjectStateError("REFERENCE_CONTRACT_SUPERSEDED_SYNTHESIS")
    synthesis_id = synthesis.get("synthesis_id")
    if not isinstance(synthesis_id, str) or not re.fullmatch(r"REFSYN-[0-9]{3,4}", synthesis_id):
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_ID_INVALID")
    if not isinstance(synthesis_ref, str) or not synthesis_ref.strip():
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_REF_INVALID")
    if (
        not isinstance(active_requirements_ref, str)
        or not active_requirements_ref.strip()
        or not isinstance(product_spec_ref, str)
        or not isinstance(approved_plan_ref, str)
        or not isinstance(product_approval_ref, str)
        or not product_approval_ref.strip()
    ):
        raise ProjectStateError("REFERENCE_CONTRACT_APPROVED_SOURCE_INVALID")
    if (
        not isinstance(plan_approval_ref, str)
        or not plan_approval_ref.strip()
    ):
        raise ProjectStateError("REFERENCE_CONTRACT_APPROVED_SOURCE_INVALID")
    document_text = _text(product_spec_text) + "\n" + _text(approved_plan_text)
    decision_ids = _ids(_REFDEC, product_spec_text, approved_plan_text)
    if not decision_ids:
        return None
    known = _decision_map(synthesis)
    exclusions = _exclusions(requirements, synthesis)
    exclusion_tokens = {item.casefold() for item in exclusions}
    source_references = [str(item) for item in synthesis.get("source_references", []) or []]
    if not source_references or any(_REF.fullmatch(item) is None for item in source_references):
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_CHAIN_INVALID")
    bindings: list[dict[str, Any]] = []
    requirement_refs = _ids(_REQ, _text(requirements), document_text)
    spec_refs = _ids(_PS, product_spec_text) or [product_spec_ref]
    plan_refs = _ids(_TASK, approved_plan_text) or [approved_plan_ref]
    acceptance_refs = _ids(_AC, product_spec_text, approved_plan_text, _text(requirements))
    if not acceptance_refs:
        raise ProjectStateError("REFERENCE_CONTRACT_ACCEPTANCE_TRACE_MISSING")
    for decision_id in decision_ids:
        entry = known.get(decision_id)
        if entry is None:
            raise ProjectStateError("REFERENCE_CONTRACT_DANGLING_DECISION:" + decision_id)
        bucket, decision = entry
        domain = str(decision.get("domain", "unknown"))
        if bucket == "avoid" or str(decision.get("user_scope_status", "include")) in {"exclude", "revoked"}:
            raise ProjectStateError("REFERENCE_CONTRACT_EXCLUSION_CONFLICT:" + decision_id)
        decision_exclusions = {
            str(item).casefold()
            for item in decision.get("explicit_exclusions", []) or []
            if str(item).strip()
        }
        if domain.casefold() in exclusion_tokens or decision_exclusions & exclusion_tokens:
            raise ProjectStateError("REFERENCE_CONTRACT_EXCLUSION_CONFLICT:" + decision_id)
        source_findings = [str(item) for item in decision.get("source_findings", []) or []]
        if not source_findings or any(_REFFND.fullmatch(item) is None for item in source_findings):
            raise ProjectStateError("REFERENCE_CONTRACT_FINDING_TRACE_MISSING:" + decision_id)
        bindings.append(
            {
                "reference_decision_id": decision_id,
                "implementation_status": "required",
                "product_spec_refs": list(spec_refs),
                "plan_refs": list(plan_refs),
                "acceptance_refs": list(acceptance_refs),
                "requirement_refs": list(requirement_refs),
                "source_finding_refs": source_findings,
                "source_reference_refs": list(source_references),
                "applies_to": [domain],
                "exclusions": list(exclusions),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "contract_kind": "approved_reference_contract",
        "status": "approved",
        "source_chain": {
            "active_requirements": active_requirements_ref,
            "reference_synthesis": synthesis_ref,
            "reference_synthesis_id": synthesis_id,
            "product_spec": product_spec_ref,
            "product_approval_record": product_approval_ref,
            "approved_plan": approved_plan_ref,
            "plan_approval_record": plan_approval_ref,
            "versions": _state_versions(state),
        },
        "reference_bindings": bindings,
        "explicit_exclusions": list(exclusions),
        "raw_reference_access": False,
        "reference_data_trust": "untrusted_reference_data",
        "evaluation_authority": False,
        "generator_authority": "approved_bindings_only",
    }
    contract_hash = hashlib.sha256(canonical_reference_contract(payload).encode("utf-8")).hexdigest()
    contract = {
        **payload,
        "contract_id": "reference-contract-" + contract_hash[:12],
        "contract_hash": contract_hash,
    }
    validate_approved_reference_contract(contract)
    return contract


def validate_approved_reference_contract(contract: Mapping[str, Any]) -> None:
    """校验 Contract 不会把 Reference 重新变成 Requirement 或 Runtime 指令。"""

    if not isinstance(contract, Mapping):
        raise ProjectStateError("REFERENCE_CONTRACT_INVALID")
    required = {
        "schema_version", "contract_kind", "status", "source_chain", "reference_bindings",
        "explicit_exclusions", "raw_reference_access", "reference_data_trust",
        "evaluation_authority", "generator_authority", "contract_id", "contract_hash",
    }
    if set(contract) != required:
        raise ProjectStateError("REFERENCE_CONTRACT_FIELDS_INVALID")
    if contract["schema_version"] != 1 or contract["contract_kind"] != "approved_reference_contract":
        raise ProjectStateError("REFERENCE_CONTRACT_SCHEMA_INVALID")
    if contract["status"] != "approved" or contract["raw_reference_access"] is not False:
        raise ProjectStateError("REFERENCE_CONTRACT_AUTHORITY_INVALID")
    if contract["reference_data_trust"] != "untrusted_reference_data" or contract["evaluation_authority"] is not False:
        raise ProjectStateError("REFERENCE_CONTRACT_TRUST_INVALID")
    if contract["generator_authority"] != "approved_bindings_only":
        raise ProjectStateError("REFERENCE_CONTRACT_GENERATOR_AUTHORITY_INVALID")
    if not isinstance(contract["contract_id"], str) or re.fullmatch(r"reference-contract-[a-f0-9]{12}", contract["contract_id"]) is None:
        raise ProjectStateError("REFERENCE_CONTRACT_ID_INVALID")
    if not isinstance(contract["contract_hash"], str) or re.fullmatch(r"[a-f0-9]{64}", contract["contract_hash"]) is None:
        raise ProjectStateError("REFERENCE_CONTRACT_HASH_INVALID")
    payload = {key: value for key, value in contract.items() if key not in {"contract_id", "contract_hash"}}
    expected_hash = hashlib.sha256(canonical_reference_contract(payload).encode("utf-8")).hexdigest()
    if expected_hash != contract["contract_hash"] or contract["contract_id"] != "reference-contract-" + expected_hash[:12]:
        raise ProjectStateError("REFERENCE_CONTRACT_HASH_MISMATCH")
    if set(contract) & _RAW_REFERENCE_KEYS:
        raise ProjectStateError("REFERENCE_CONTRACT_RAW_REFERENCE_FORBIDDEN")
    exclusions = contract["explicit_exclusions"]
    if not isinstance(exclusions, list) or any(
        not isinstance(item, str) or not item.strip() for item in exclusions
    ):
        raise ProjectStateError("REFERENCE_CONTRACT_EXCLUSIONS_INVALID")
    bindings = contract["reference_bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise ProjectStateError("REFERENCE_CONTRACT_BINDINGS_MISSING")
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_INVALID")
        expected = {
            "reference_decision_id", "implementation_status", "product_spec_refs", "plan_refs",
            "acceptance_refs", "requirement_refs", "source_finding_refs", "source_reference_refs",
            "applies_to", "exclusions",
        }
        if set(binding) != expected:
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_FIELDS_INVALID")
        decision_id = binding["reference_decision_id"]
        if not isinstance(decision_id, str) or _REFDEC.fullmatch(decision_id) is None or decision_id in seen:
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_ID_INVALID")
        seen.add(decision_id)
        if binding["implementation_status"] != "required":
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_STATUS_INVALID")
        for key in (
            "product_spec_refs",
            "plan_refs",
            "acceptance_refs",
            "requirement_refs",
            "source_finding_refs",
            "source_reference_refs",
            "applies_to",
            "exclusions",
        ):
            value = binding[key]
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ProjectStateError("REFERENCE_CONTRACT_BINDING_LIST_INVALID:" + key)
        if any(_AC.fullmatch(item) is None for item in binding["acceptance_refs"]):
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_ACCEPTANCE_INVALID")
        if any(_REQ.fullmatch(item) is None for item in binding["requirement_refs"]):
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_REQUIREMENT_INVALID")
        if any(_REFFND.fullmatch(item) is None for item in binding["source_finding_refs"]):
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_FINDING_INVALID")
        if any(_REF.fullmatch(item) is None for item in binding["source_reference_refs"]):
            raise ProjectStateError("REFERENCE_CONTRACT_BINDING_REFERENCE_INVALID")
    source_chain = contract["source_chain"]
    source_fields = {
        "active_requirements",
        "reference_synthesis",
        "reference_synthesis_id",
        "product_spec",
        "product_approval_record",
        "approved_plan",
        "plan_approval_record",
        "versions",
    }
    if not isinstance(source_chain, Mapping) or set(source_chain) != source_fields:
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_CHAIN_INVALID")
    for key in (
        "active_requirements",
        "reference_synthesis",
        "product_spec",
        "product_approval_record",
        "approved_plan",
        "plan_approval_record",
    ):
        if not isinstance(source_chain[key], str) or not source_chain[key].strip():
            raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_CHAIN_INVALID")
    if re.fullmatch(r"REFSYN-[0-9]{3,4}", str(source_chain["reference_synthesis_id"])) is None:
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_CHAIN_INVALID")
    if not isinstance(source_chain["versions"], Mapping):
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_CHAIN_INVALID")
    serialized = canonical_reference_contract(contract)
    if _SECRET.search(serialized) or "evaluation pass" in serialized.casefold() or "conformance pass" in serialized.casefold():
        raise ProjectStateError("REFERENCE_CONTRACT_UNSAFE_CONTENT")


__all__ = [
    "build_approved_reference_contract",
    "canonical_reference_contract",
    "reference_contract_context_hash",
    "validate_approved_reference_contract",
]
