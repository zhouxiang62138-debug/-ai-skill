"""E1 Evaluator Independence Hardening 的确定性策略与 Gate。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]
_SHA256_LENGTH = 64
_PROVENANCE = frozenset(
    {"GENERATOR_PROVIDED", "EVALUATOR_REPRODUCED", "RUNTIME_VERIFIED", "EXTERNAL"}
)
_INDEPENDENT_PROVENANCE = frozenset({"EVALUATOR_REPRODUCED", "RUNTIME_VERIFIED"})
_REPRODUCTION_KEYS = (
    "build",
    "required_tests",
    "browser_required_scenarios",
    "regression",
)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_LENGTH and all(
        item in "0123456789abcdef" for item in value
    )


def _timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True)
class EvaluatorIndependencePolicy:
    """E1 的不可变运行策略。"""

    version: int
    upgrade_id: str
    fresh_invocation_required: bool
    inherit_previous_invocation_history: bool
    generator_claims_are_evidence: bool
    excluded_fields: frozenset[str]
    excluded_reference_prefixes: tuple[str, ...]
    excluded_source_labels: frozenset[str]
    allowed_provenance: frozenset[str]
    critical_provenance: frozenset[str]
    reexecute: Mapping[str, bool]
    gate_errors: Mapping[str, str]

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "EvaluatorIndependencePolicy":
        path = Path(config_path) if config_path else _ROOT / "config" / "evaluation_independence.yaml"
        try:
            document = parse_project_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_UNAVAILABLE") from exc
        if not isinstance(document, Mapping) or document.get("version") != 1:
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        context = document.get("context")
        provenance = document.get("evidence_provenance")
        reexecute = document.get("reexecute")
        gate_errors = document.get("gate_errors")
        if not isinstance(context, Mapping) or not isinstance(provenance, Mapping):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if not isinstance(reexecute, Mapping) or not isinstance(gate_errors, Mapping):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        excluded_fields = context.get("excluded_fields")
        excluded_prefixes = context.get("excluded_reference_prefixes")
        excluded_labels = context.get("excluded_source_labels")
        allowed = provenance.get("allowed")
        critical = provenance.get("required_for_critical")
        if not all(
            isinstance(value, list) and all(isinstance(item, str) and item for item in value)
            for value in (excluded_fields, excluded_prefixes, excluded_labels, allowed, critical)
        ):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        allowed_set = frozenset(allowed)
        critical_set = frozenset(critical)
        if not critical_set or not critical_set <= allowed_set or not critical_set <= _INDEPENDENT_PROVENANCE:
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if set(allowed_set) != set(_PROVENANCE):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if any(not isinstance(key, str) or not isinstance(value, bool) for key, value in reexecute.items()):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in gate_errors.items()):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if document.get("upgrade_id") != "E1":
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        return cls(
            version=1,
            upgrade_id="E1",
            fresh_invocation_required=document.get("fresh_invocation_required") is True,
            inherit_previous_invocation_history=document.get("inherit_previous_invocation_history") is True,
            generator_claims_are_evidence=document.get("generator_claims_are_evidence") is True,
            excluded_fields=frozenset(excluded_fields),
            excluded_reference_prefixes=tuple(excluded_prefixes),
            excluded_source_labels=frozenset(excluded_labels),
            allowed_provenance=allowed_set,
            critical_provenance=critical_set,
            reexecute=dict(reexecute),
            gate_errors=dict(gate_errors),
        )


def code_snapshot_hash(project_root: str | Path) -> str:
    """对当前项目 code/ 做稳定摘要，避免旧版本 Evidence 跨 revision 复用。"""

    root = Path(project_root).resolve()
    code_root = root / "code"
    digest = hashlib.sha256()
    files = sorted(
        item for item in code_root.rglob("*") if item.is_file()
    ) if code_root.is_dir() else []
    for item in files:
        relative = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(item.read_bytes()).digest())
    if not files:
        digest.update(b"<empty-code>")
    return digest.hexdigest()


def validate_evaluator_context(
    context: Mapping[str, Any],
    *,
    policy: EvaluatorIndependencePolicy | None = None,
) -> tuple[bool, str]:
    """检查 Context Manifest 是否真的属于 E1 的最小必要盲审范围。"""

    current = policy or EvaluatorIndependencePolicy.load()
    if not isinstance(context, Mapping) or context.get("role") != "evaluator":
        return False, current.gate_errors["context"]
    if context.get("context_type") != "EVALUATOR_INDEPENDENT":
        return False, current.gate_errors["context"]
    excluded = context.get("excluded_sources")
    if not isinstance(excluded, list) or not current.excluded_source_labels <= set(excluded):
        return False, current.gate_errors["context"]
    sources = context.get("sources")
    if not isinstance(sources, list):
        return False, current.gate_errors["context"]
    references = {
        str(item.get("reference"))
        for item in sources
        if isinstance(item, Mapping) and isinstance(item.get("reference"), str)
    }
    if "project.yaml" not in references or not any("plan" in item.casefold() for item in references):
        return False, current.gate_errors["context"]
    for item in sources:
        if not isinstance(item, Mapping):
            return False, current.gate_errors["context"]
        reference = str(item.get("reference", "")).replace("\\", "/")
        source_type = str(item.get("source_type", ""))
        if source_type in {"generator_chat_history", "generator_reasoning", "generator_self_assessment"}:
            return False, current.gate_errors["context"]
        if any(reference.casefold().startswith(prefix.casefold()) for prefix in current.excluded_reference_prefixes):
            return False, current.gate_errors["context"]
    return True, "evaluator_context_isolated"


def validate_evaluator_evidence(
    response: Mapping[str, Any],
    request: Any,
    project_root: str | Path,
    *,
    policy: EvaluatorIndependencePolicy | None = None,
    session_store: Any | None = None,
) -> dict[str, Any]:
    """把模型提交的验收结果约束为 CLAIM → VERIFY → RESULT。"""

    current = policy or EvaluatorIndependencePolicy.load()
    if not isinstance(response, Mapping):
        return {"passed": False, "evidence_refs": [], "details": "MODEL_OUTPUT_INVALID"}
    independent = response.get("evaluator_independence")
    if not isinstance(independent, Mapping):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["pass"]}
    context = getattr(request, "context", None)
    context = context if isinstance(context, Mapping) else {}
    context_ok, context_details = validate_evaluator_context(context, policy=current)
    if not context_ok:
        return {"passed": False, "evidence_refs": [], "details": context_details}
    invocation_id = getattr(request, "invocation_id", None)
    context_id = getattr(request, "context_manifest_id", None) or context.get("context_id")
    source_revision = getattr(request, "source_revision", None)
    expected_snapshot = getattr(request, "code_snapshot_hash", None)
    if (
        current.fresh_invocation_required
        and (independent.get("fresh_invocation") is not True or independent.get("evaluator_invocation_id") != invocation_id)
    ):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["fresh_invocation"]}
    if independent.get("generator_claims_are_evidence") is not False:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
    if independent.get("context_manifest_id") != context_id:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["context"]}
    if independent.get("project_revision") != source_revision:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["revision"]}
    if independent.get("code_snapshot_hash") != expected_snapshot or not _sha256(expected_snapshot):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["revision"]}
    reproduction = independent.get("reproduction")
    if not isinstance(reproduction, Mapping):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["reproduction"]}
    for name in _REPRODUCTION_KEYS:
        if current.reexecute.get(name) is True and reproduction.get(name) not in {"PASS", "NOT_APPLICABLE"}:
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["reproduction"]}
    for name in ("blocking_issues", "critical_issues"):
        if not isinstance(independent.get(name), list) or independent.get(name):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["pass"]}
    if independent.get("protected_artifacts_unchanged") is not True:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["pass"]}
    if independent.get("prior_blocking_issues_resolved") is not True:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["pass"]}
    if independent.get("runtime_verifiers_passed") is not True:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["pass"]}

    criteria = independent.get("required_acceptance_criteria")
    evidence = independent.get("evidence")
    if not isinstance(criteria, list) or not criteria or any(not _nonempty(item) for item in criteria):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
    if not isinstance(evidence, list) or not evidence:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
    by_criterion: dict[str, list[Mapping[str, Any]]] = {}
    refs: list[str] = []
    root = Path(project_root).resolve()
    actual_snapshot = code_snapshot_hash(root)
    if expected_snapshot != actual_snapshot:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["revision"]}
    for item in evidence:
        if not isinstance(item, Mapping):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        criterion = item.get("acceptance_criterion_id")
        provenance = item.get("provenance")
        if not _nonempty(criterion) or provenance not in current.allowed_provenance:
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        required = item.get("required", True)
        if not isinstance(required, bool) or item.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        if item.get("result") == "PASS" and required and provenance not in current.critical_provenance:
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        required_strings = ("tool_call_id", "attempt_id", "result_hash", "code_snapshot_hash")
        if any(not _nonempty(item.get(name)) for name in required_strings):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        if not _sha256(item.get("result_hash")) or item.get("code_snapshot_hash") != expected_snapshot:
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["revision"]}
        if item.get("project_revision") != source_revision or not _timestamp(item.get("timestamp")):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["revision"]}
        if not isinstance(item.get("command"), list) or not item.get("command"):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        if not isinstance(item.get("environment"), Mapping):
            return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
        if session_store is not None:
            try:
                tool_call = session_store.get_tool_call(
                    str(getattr(request, "session_id", "")),
                    str(item["tool_call_id"]),
                )
                attempt = session_store.get_tool_attempt(
                    str(getattr(request, "session_id", "")),
                    str(item["attempt_id"]),
                )
                if (
                    tool_call.get("status") != "SUCCEEDED"
                    or attempt.get("status") != "SUCCEEDED"
                    or attempt.get("tool_call_id") != item["tool_call_id"]
                    or tool_call.get("result_hash") != item["result_hash"]
                    or attempt.get("result_hash") != item["result_hash"]
                    or attempt.get("code_snapshot_hash") != expected_snapshot
                ):
                    return {
                        "passed": False,
                        "evidence_refs": [],
                        "details": current.gate_errors["provenance"],
                    }
                session_store.read_tool_result(
                    str(tool_call["result_reference"]),
                    str(tool_call["result_hash"]),
                )
            except Exception:
                return {
                    "passed": False,
                    "evidence_refs": [],
                    "details": current.gate_errors["provenance"],
                }
        by_criterion.setdefault(str(criterion), []).append(item)
        refs.extend([str(item["tool_call_id"]), str(item["attempt_id"])])
    missing = [item for item in criteria if str(item) not in by_criterion]
    if missing:
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
    if any(
        not any(item.get("result") == "PASS" and item.get("provenance") in current.critical_provenance for item in items)
        for items in by_criterion.values()
    ):
        return {"passed": False, "evidence_refs": [], "details": current.gate_errors["provenance"]}
    return {
        "passed": True,
        "evidence_refs": list(dict.fromkeys(refs)),
        "details": "e1_deterministic_pass_gate",
        "code_snapshot_hash": actual_snapshot,
    }


__all__ = [
    "EvaluatorIndependencePolicy",
    "code_snapshot_hash",
    "validate_evaluator_context",
    "validate_evaluator_evidence",
]
