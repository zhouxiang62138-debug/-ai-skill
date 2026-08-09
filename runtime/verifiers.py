"""Runtime-owned Role Verifier Registry。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import load_project_state

from .errors import RuntimeValidationError


RUNTIME_FIELDS = frozenset({"next_role", "active_module", "runtime", "schema_version"})
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


class RuntimeVerifierRegistry:
    """由 Runtime 固定分派的 Verifier；正式入口不接受任意 lambda。"""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()

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
            return {"passed": True, "evidence_refs": ["runtime:contract_preflight"], "details": "runtime_execution"}
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
            return {"passed": True, "evidence_refs": refs, "details": "runtime_evidence_reference"}
        return {"passed": False, "evidence_refs": [], "details": "VERIFIER_NOT_REGISTERED:" + step}


__all__ = ["RuntimeVerifierRegistry"]
