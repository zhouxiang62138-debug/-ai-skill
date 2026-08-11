"""Runtime 强制执行的 Role Phase Runner。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from scripts.approval import validate_generator_gate
from scripts.project_state import ProjectStateError, load_project_state, parse_project_yaml

from .context import ContextBuildRequest, ContextBuilder
from .contract_preflight import run_contract_preflight
from .errors import RuntimeValidationError
from .attestation import required_steps_hash
from .project_revision import runtime_projection
from .reference_contract import (
    build_reference_contract_for_project,
    reference_contract_context_hash,
)
from .verifiers import RuntimeVerifierRegistry


CORE_ROLES = frozenset({"planner", "generator", "evaluator"})
DEFAULT_PHASE_STEPS: dict[str, tuple[str, ...]] = {
    "planner": ("source_chain", "proposal_or_plan", "handoff"),
    "generator": ("contract_preflight", "implementation", "tests", "handoff"),
    "evaluator": (
        "browser_scenarios",
        "feature_completeness",
        "build_test_regression",
        "evidence_manifest",
        "issue_package",
        "candidate",
        "evaluation_transaction",
    ),
}


class ModelInvocationAdapter(Protocol):
    """外部模型调用适配器；Runtime 不绑定任何厂商 SDK。"""

    def invoke(self, request: "ModelInvocationRequest") -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class ModelInvocationRequest:
    session_id: str
    run_id: str
    invocation_id: str
    role: str
    phase: str
    context: Mapping[str, Any]
    required_steps: tuple[str, ...]


@dataclass(frozen=True)
class PhaseRunResult:
    status: str
    role: str
    phase: str
    context_id: str | None
    invocation_id: str | None
    attestation_id: str | None
    completed_steps: tuple[str, ...]
    failure_reason: str | None = None
    cas_result: Mapping[str, Any] | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "role": self.role,
            "phase": self.phase,
            "context_id": self.context_id,
            "invocation_id": self.invocation_id,
            "attestation_id": self.attestation_id,
            "completed_steps": list(self.completed_steps),
            "failure_reason": self.failure_reason,
            "cas_result": dict(self.cas_result) if self.cas_result else None,
            "replayed": self.replayed,
        }


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class PhaseRunner:
    """将 Role Run、Context、模型 Invocation、必需步骤与 CAS 串成闭环。"""

    def __init__(
        self,
        orchestrator: Any,
        model_adapter: ModelInvocationAdapter,
        *,
        context_builder: ContextBuilder | None = None,
        phase_steps: Mapping[str, tuple[str, ...]] | None = None,
        gate_verifiers: Mapping[str, Callable[[ModelInvocationRequest, Mapping[str, Any]], bool]] | None = None,
        verifier_registry: RuntimeVerifierRegistry | None = None,
        test_only_verifiers: bool = False,
    ) -> None:
        self.orchestrator = orchestrator
        self.model_adapter = model_adapter
        self.context_builder = context_builder or ContextBuilder(orchestrator.store)
        self.phase_steps = dict(phase_steps or DEFAULT_PHASE_STEPS)
        for role, defaults in DEFAULT_PHASE_STEPS.items():
            configured = tuple(self.phase_steps.get(role, defaults))
            if not set(defaults) <= set(configured):
                raise RuntimeValidationError("PHASE_CONFIG_REQUIRED_STEPS_SHRUNK:" + role)
            if len(set(configured)) != len(configured) or any(not isinstance(item, str) or not item for item in configured):
                raise RuntimeValidationError("PHASE_CONFIG_REQUIRED_STEPS_INVALID:" + role)
            self.phase_steps[role] = configured
        if gate_verifiers and not test_only_verifiers:
            raise RuntimeValidationError("FORMAL_VERIFIER_MUST_USE_RUNTIME_REGISTRY")
        self.gate_verifiers = dict(gate_verifiers or {})
        self.verifier_registry = verifier_registry
        self.test_only_verifiers = test_only_verifiers

    def _replay(self, run: Mapping[str, Any]) -> PhaseRunResult | None:
        if run.get("status") not in {"COMPLETED", "FAILED"}:
            return None
        raw = run.get("result_json")
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return PhaseRunResult(
            status=str(value.get("status", run["status"])),
            role=str(value.get("role", run.get("role", "unknown"))),
            phase=str(value.get("phase", "unknown")),
            context_id=value.get("context_id"),
            invocation_id=value.get("invocation_id"),
            attestation_id=value.get("attestation_id"),
            completed_steps=tuple(value.get("completed_steps", ())),
            failure_reason=value.get("failure_reason"),
            cas_result=value.get("cas_result"),
            replayed=True,
        )

    def _generator_preflight(
        self,
        root: Path,
        *,
        feature: Mapping[str, Any] | None,
        approved_plan: str | None,
        approved_requirements: set[str],
        approved_acceptance_criteria: set[str],
    ) -> Mapping[str, Any]:
        state = load_project_state(root / "project.yaml")
        errors = validate_generator_gate(state, root)
        if errors:
            raise RuntimeValidationError(
                "GENERATOR_PREFLIGHT_FAILED:approved_source_chain_invalid:" + ";".join(errors)
            )
        protected_fields = (
            "approved_plan",
            "active_product_spec",
            "active_requirements",
            "plan_approval_record",
        )
        sources: dict[str, Any] = {}
        documents: list[Any] = []
        for field in protected_fields:
            reference = state.get(field)
            if not isinstance(reference, str) or not reference.strip():
                raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:protected_source_missing:" + field)
            normalized = reference.replace("\\", "/")
            candidate = (root / reference).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:protected_source_escape:" + field) from exc
            if ".." in normalized.split("/") or not candidate.is_file():
                raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:protected_source_invalid:" + field)
            raw = candidate.read_text(encoding="utf-8")
            try:
                document = parse_project_yaml(raw)
            except Exception:
                document = raw
            sources[field] = reference
            documents.append(document)
        if approved_plan is not None and approved_plan != state.get("approved_plan"):
            raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:caller_approved_plan_mismatch")
        if approved_requirements and not approved_requirements <= self._extract_ids(documents, "REQ-"):
            raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:caller_requirement_mismatch")
        if approved_acceptance_criteria and not approved_acceptance_criteria <= self._extract_ids(documents, "AC-"):
            raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:caller_acceptance_criterion_mismatch")
        derived_feature = self._derive_feature(documents, state)
        contract = run_contract_preflight(
            root,
            feature=derived_feature,
            approved_plan=str(state["approved_plan"]),
            approved_requirements=self._extract_ids(documents, "REQ-"),
            approved_acceptance_criteria=self._extract_ids(documents, "AC-"),
        )
        if not contract.passed:
            raise RuntimeValidationError(
                "GENERATOR_PREFLIGHT_FAILED:contract:" + ";".join(contract.errors)
            )
        try:
            reference_contract = build_reference_contract_for_project(root)
        except ProjectStateError as exc:
            raise RuntimeValidationError(
                "GENERATOR_PREFLIGHT_FAILED:reference_contract:" + str(exc)
            ) from exc
        evidence_refs = ["runtime:generator_preflight"]
        if reference_contract is not None:
            evidence_refs.append(
                "runtime:approved-reference-contract:"
                + str(reference_contract["contract_hash"])
            )
        return {
            "passed": True,
            "evidence_refs": evidence_refs,
            "reference_contract": reference_contract,
            "reference_contract_hash": (
                reference_contract["contract_hash"]
                if reference_contract is not None
                else None
            ),
        }

    @staticmethod
    def _validate_reference_context(
        preflight: Mapping[str, Any],
        context: Any,
    ) -> None:
        """确保 Context 中的契约与 Generator Preflight 是同一版本。"""

        contract = preflight.get("reference_contract")
        sources = [
            source
            for source in getattr(context, "sources", ())
            if getattr(source, "source_type", None) == "approved_reference_bindings"
        ]
        if contract is None:
            if sources:
                raise RuntimeValidationError(
                    "GENERATOR_PREFLIGHT_FAILED:reference_contract_context_unexpected"
                )
            return
        if len(sources) != 1:
            raise RuntimeValidationError(
                "GENERATOR_PREFLIGHT_FAILED:reference_contract_context_missing"
            )
        source = sources[0]
        expected_hash = reference_contract_context_hash(contract)
        if (
            source.content_hash != expected_hash
            or str(contract["contract_id"]) not in source.reference
        ):
            raise RuntimeValidationError(
                "GENERATOR_PREFLIGHT_FAILED:reference_contract_context_mismatch"
            )

    @staticmethod
    def _extract_ids(documents: list[Any], prefix: str) -> set[str]:
        found: set[str] = set()
        for document in documents:
            text = json.dumps(document, ensure_ascii=False) if not isinstance(document, str) else document
            found.update(re.findall(rf"{re.escape(prefix)}[A-Za-z0-9_-]+", text))
        return found

    @staticmethod
    def _derive_feature(documents: list[Any], state: Mapping[str, Any]) -> dict[str, Any]:
        """从受保护 Plan/Spec 推导风险事实；无法确定时 fail closed。"""

        feature: dict[str, Any] = {}
        found_manifest = False
        for document in documents:
            if not isinstance(document, Mapping):
                continue
            candidate = document.get("feature_manifest", document)
            if isinstance(candidate, Mapping):
                found_manifest = found_manifest or "feature_manifest" in document
                for key in (
                    "feature_id",
                    "risk_tags",
                    "irreversible",
                    "critical_workflow_pages",
                    "acceptance_criteria_count",
                ):
                    if key in candidate:
                        feature[key] = candidate[key]
        if "risk_tags" not in feature:
            feature["risk_tags"] = []
        if "irreversible" not in feature:
            feature["irreversible"] = False
        if "critical_workflow_pages" not in feature:
            feature["critical_workflow_pages"] = 0
        if "acceptance_criteria_count" not in feature:
            feature["acceptance_criteria_count"] = 0
        if not isinstance(feature["risk_tags"], list) or not isinstance(feature["irreversible"], bool):
            raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:feature_risk_undetermined")
        if not found_manifest:
            raise RuntimeValidationError("GENERATOR_PREFLIGHT_FAILED:feature_risk_undetermined")
        feature.setdefault("feature_id", state.get("active_plan") or "approved-feature")
        return feature

    def _fail(
        self,
        session_id: str,
        run_id: str,
        *,
        role: str,
        phase: str,
        reason: str,
        context_id: str | None = None,
        invocation_id: str | None = None,
        attestation_id: str | None = None,
    ) -> PhaseRunResult:
        safe_reason = str(reason)[:1000]
        if invocation_id is not None:
            self.orchestrator.store.complete_model_invocation(
                session_id,
                invocation_id,
                status="FAILED",
                result_hash=_digest({"status": "FAILED", "reason": safe_reason}),
            )
        result = PhaseRunResult(
            status="FAILED",
            role=role,
            phase=phase,
            context_id=context_id,
            invocation_id=invocation_id,
            attestation_id=attestation_id,
            completed_steps=(),
            failure_reason=safe_reason,
        )
        self.orchestrator.fail_step(session_id, run_id, result.to_dict())
        return result

    def run(
        self,
        session_id: str,
        run_id: str,
        lease_token: str,
        *,
        phase: str = "main",
        role: str | None = None,
        required_steps: tuple[str, ...] | None = None,
        additional_references: tuple[str, ...] = (),
        idempotency_key: str | None = None,
        feature: Mapping[str, Any] | None = None,
        approved_plan: str | None = None,
        approved_requirements: set[str] | None = None,
        approved_acceptance_criteria: set[str] | None = None,
    ) -> PhaseRunResult:
        """运行一阶段；失败不会提交业务状态，重复调用只返回 durable 结果。"""

        run = self.orchestrator.store.get_role_run(session_id, run_id)
        replay = self._replay(run)
        if replay is not None:
            return replay
        resolved_role = role or str(run.get("role"))
        if resolved_role not in CORE_ROLES or run.get("role") != resolved_role:
            raise RuntimeValidationError("PHASE_ROLE_NOT_ALLOWED")
        if run.get("status") != "STARTED":
            raise RuntimeValidationError("PHASE_ROLE_RUN_NOT_STARTED")
        lease = self.orchestrator.leases.get(session_id)
        if lease.worker_id != run.get("worker_id"):
            raise RuntimeValidationError("PHASE_WORKER_MISMATCH")
        self.orchestrator.leases.assert_valid(
            session_id,
            str(run["worker_id"]),
            lease.lease_version,
            lease_token,
        )
        runtime_steps = tuple(self.phase_steps.get(resolved_role, DEFAULT_PHASE_STEPS[resolved_role]))
        requested_steps = tuple(required_steps or ())
        if requested_steps and (not all(isinstance(item, str) and item for item in requested_steps) or len(set(requested_steps)) != len(requested_steps)):
            raise RuntimeValidationError("PHASE_REQUIRED_STEPS_INVALID")
        if not runtime_steps or len(set(runtime_steps)) != len(runtime_steps):
            raise RuntimeValidationError("PHASE_RUNTIME_REQUIRED_STEPS_INVALID")
        if requested_steps and not set(runtime_steps) <= set(requested_steps):
            return self._fail(
                session_id,
                run_id,
                role=resolved_role,
                phase=phase,
                reason="PHASE_REQUIRED_STEPS_SHRUNK",
            )
        steps = requested_steps or runtime_steps
        allowed_steps = set(DEFAULT_PHASE_STEPS[resolved_role]) | {"evidence", "regression", "browser_acceptance"}
        if any(item not in allowed_steps for item in steps):
            return self._fail(
                session_id,
                run_id,
                role=resolved_role,
                phase=phase,
                reason="PHASE_REQUIRED_STEP_UNKNOWN",
            )
        context_id: str | None = None
        invocation_id: str | None = None
        attestation_id: str | None = None
        preflight_result: Mapping[str, Any] | None = None
        try:
            if resolved_role == "generator":
                preflight_result = self._generator_preflight(
                    Path(self.orchestrator.root),
                    feature=feature,
                    approved_plan=approved_plan,
                    approved_requirements=approved_requirements or set(),
                    approved_acceptance_criteria=approved_acceptance_criteria or set(),
                )
            context = self.context_builder.build(
                ContextBuildRequest(
                    session_id,
                    run_id,
                    resolved_role,
                    tuple(additional_references),
                )
            )
            if resolved_role == "generator" and preflight_result is not None:
                self._validate_reference_context(preflight_result, context)
            context_id = context.context_id
            invocation = self.orchestrator.store.create_model_invocation(
                session_id,
                run_id,
                resolved_role,
                context.context_id,
                idempotency_key=idempotency_key or f"phase:{run_id}:{phase}",
            )
            invocation_id = str(invocation["invocation_id"])
            request = ModelInvocationRequest(
                session_id=session_id,
                run_id=run_id,
                invocation_id=invocation_id,
                role=resolved_role,
                phase=phase,
                context=context.manifest,
                required_steps=steps,
            )
            response = self.model_adapter.invoke(request)
            if not isinstance(response, Mapping):
                raise RuntimeValidationError("MODEL_OUTPUT_INVALID")
            forbidden = {
                "next_role",
                "active_module",
                "runtime",
                "schema_version",
                "cas_commit",
                "reference_contract",
                "approved_reference_contract",
            }
            illegal = sorted(forbidden & set(response))
            if illegal:
                if "cas_commit" in illegal:
                    raise RuntimeValidationError("MODEL_OUTPUT_CAS_COMMIT_FORBIDDEN")
                if "next_role" in illegal:
                    raise RuntimeValidationError("MODEL_OUTPUT_NEXT_ROLE_FORBIDDEN")
                raise RuntimeValidationError("MODEL_OUTPUT_RUNTIME_FIELD_FORBIDDEN:" + ",".join(illegal))
            completed = response.get("completed_steps")
            if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
                raise RuntimeValidationError("MODEL_OUTPUT_STEPS_INVALID")
            if len(set(completed)) != len(completed) or set(completed) != set(steps):
                missing = sorted(set(steps) - set(completed))
                raise RuntimeValidationError(
                    "PHASE_REQUIRED_STEP_MISSING:" + ",".join(missing)
                )
            verifier_results: dict[str, Any] = {}
            if resolved_role == "evaluator" and self.verifier_registry is None and not self.gate_verifiers:
                raise RuntimeValidationError("EVALUATOR_GATE_VERIFIER_MISSING:" + steps[0])
            if self.verifier_registry is None and not self.test_only_verifiers:
                raise RuntimeValidationError("RUNTIME_VERIFIER_REGISTRY_REQUIRED")
            for step in steps:
                if self.verifier_registry is not None:
                    verdict = self.verifier_registry.verify(
                        step,
                        request,
                        response,
                        preflight=preflight_result,
                    )
                    verifier_results[step] = verdict
                    if verdict.get("passed") is not True:
                        raise RuntimeValidationError(
                            "PHASE_VERIFIER_FAILED:" + step + ":" + str(verdict.get("details", ""))
                        )
                else:
                    verifier = self.gate_verifiers.get(step)
                    if resolved_role == "evaluator" and verifier is None:
                        raise RuntimeValidationError("EVALUATOR_GATE_VERIFIER_MISSING:" + step)
                    if verifier is not None:
                        passed = verifier(request, response) is True
                        verdict = {
                            "passed": passed,
                            "evidence_refs": [f"test-only:{step}"],
                            "details": "test-only-verifier",
                        }
                        verifier_results[step] = verdict
                        if not passed:
                            raise RuntimeValidationError("EVALUATOR_GATE_FAILED:" + step)
                    else:
                        verifier_results[step] = {
                            "passed": True,
                            "evidence_refs": [f"declaration:{step}"],
                            "details": "legacy-non-evaluator-declaration",
                        }
            attestation = self.orchestrator.store.create_phase_attestation(
                session_id=session_id,
                run_id=run_id,
                role=resolved_role,
                project_revision=int(runtime_projection(load_project_state(Path(self.orchestrator.root) / "project.yaml"))["revision"]),
                context_id=context_id,
                invocation_id=invocation_id,
                required_steps_hash=required_steps_hash(steps),
                verifier_results=verifier_results,
                idempotency_key=f"phase-attestation:{idempotency_key or run_id}:{phase}",
            )
            attestation_id = str(attestation["attestation_id"])
            cas_result: Mapping[str, Any] | None = None
            transition_intent = response.get("transition_intent")
            if transition_intent is not None:
                if not isinstance(transition_intent, Mapping):
                    raise RuntimeValidationError("MODEL_TRANSITION_INTENT_INVALID")
                if set(transition_intent) - {"source_status", "target_status", "changed_fields", "expected_revision", "idempotency_key"}:
                    raise RuntimeValidationError("MODEL_TRANSITION_INTENT_INVALID")
                cas_result = self.orchestrator.commit_step(
                    session_id,
                    run_id,
                    lease_token,
                    {**dict(transition_intent), "attestation_id": attestation_id},
                )
            result = PhaseRunResult(
                status="COMMITTED" if cas_result is not None else "COMPLETED",
                role=resolved_role,
                phase=phase,
                context_id=context_id,
                invocation_id=invocation_id,
                attestation_id=attestation_id,
                completed_steps=tuple(completed),
                cas_result=cas_result,
            )
            self.orchestrator.store.complete_model_invocation(
                session_id,
                invocation_id,
                result_hash=_digest(result.to_dict()),
            )
            if cas_result is None:
                self.orchestrator.store.complete_role_run(session_id, run_id, result.to_dict())
                self.orchestrator.leases.release(
                    session_id,
                    str(run["worker_id"]),
                    lease.lease_version,
                    lease_token,
                )
            return result
        except Exception as exc:
            return self._fail(
                session_id,
                run_id,
                role=resolved_role,
                phase=phase,
                reason=str(exc),
                context_id=context_id,
                invocation_id=invocation_id,
                attestation_id=attestation_id,
            )


__all__ = [
    "CORE_ROLES",
    "DEFAULT_PHASE_STEPS",
    "ModelInvocationAdapter",
    "ModelInvocationRequest",
    "PhaseRunResult",
    "PhaseRunner",
]
