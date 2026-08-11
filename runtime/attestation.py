"""Phase Attestation 的 Runtime-owned 结构与摘要工具。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .errors import RuntimeValidationError


def required_steps_hash(steps: tuple[str, ...]) -> str:
    """对 Runtime 最终步骤集合计算稳定摘要。"""

    if not steps or len(set(steps)) != len(steps):
        raise RuntimeValidationError("ATTESTATION_REQUIRED_STEPS_INVALID")
    payload = json.dumps(list(steps), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def attestation_hash(fields: Mapping[str, Any]) -> str:
    """只对受控字段做摘要，不保存模型输出正文。"""

    payload = json.dumps(
        dict(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_verifier_results(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """校验 Verifier 结果和证据引用的最小结构。"""

    if not isinstance(value, Mapping) or not value:
        raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
    safe: dict[str, Any] = {}
    evidence: list[str] = []
    for name, raw in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, Mapping):
            raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
        passed = raw.get("passed")
        refs = raw.get("evidence_refs", [])
        if not isinstance(passed, bool) or not isinstance(refs, list):
            raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
        if any(not isinstance(item, str) or not item.strip() for item in refs):
            raise RuntimeValidationError("ATTESTATION_EVIDENCE_REFS_INVALID")
        if not passed:
            raise RuntimeValidationError("ATTESTATION_VERIFIER_FAILED:" + name)
        safe[name] = {
            "passed": True,
            "evidence_refs": list(refs),
            "details": str(raw.get("details", ""))[:1000],
        }
        reference_gate = raw.get("reference_gate")
        if reference_gate is not None:
            if not isinstance(reference_gate, Mapping):
                raise RuntimeValidationError("ATTESTATION_REFERENCE_GATE_INVALID")
            contract_id = reference_gate.get("contract_id")
            contract_hash = reference_gate.get("contract_hash")
            binding_results = reference_gate.get("binding_results")
            if (
                not isinstance(contract_id, str)
                or not re.fullmatch(r"reference-contract-[a-f0-9]{12}", contract_id)
                or not isinstance(contract_hash, str)
                or not re.fullmatch(r"[a-f0-9]{64}", contract_hash)
                or reference_gate.get("result") not in {"PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"}
                or not isinstance(binding_results, list)
            ):
                raise RuntimeValidationError("ATTESTATION_REFERENCE_GATE_INVALID")
            safe_bindings: list[dict[str, Any]] = []
            for binding in binding_results:
                if not isinstance(binding, Mapping):
                    raise RuntimeValidationError("ATTESTATION_REFERENCE_BINDING_INVALID")
                decision_id = binding.get("reference_decision_id")
                evidence_refs = binding.get("evidence_refs", [])
                if (
                    not isinstance(decision_id, str)
                    or not re.fullmatch(r"REFDEC-[0-9]{3,4}", decision_id)
                    or binding.get("result") not in {"PASS", "FAIL", "BLOCKED", "UNVERIFIED"}
                    or not isinstance(evidence_refs, list)
                    or any(not isinstance(item, str) or not item.strip() for item in evidence_refs)
                ):
                    raise RuntimeValidationError("ATTESTATION_REFERENCE_BINDING_INVALID")
                safe_bindings.append(
                    {
                        "reference_decision_id": decision_id,
                        "result": binding["result"],
                        "capability": str(binding.get("capability", "")),
                        "evidence_refs": list(evidence_refs),
                    }
                )
            safe[name]["reference_gate"] = {
                "gate_id": "GATE-REFERENCE-CONFORMANCE",
                "contract_id": contract_id,
                "contract_hash": contract_hash,
                "result": reference_gate["result"],
                "binding_results": safe_bindings,
            }
        evidence.extend(refs)
    return safe, list(dict.fromkeys(evidence))


__all__ = ["attestation_hash", "required_steps_hash", "validate_verifier_results"]
