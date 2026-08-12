"""F14-C Post-Verification：合法项目 smoke、错误率和 Coverage 语义边界。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime.context import (
    ContextBuildRequest,
    ContextBuilder,
    ContextSemanticModel,
    ContextUnit,
    EnforcedCoverageGate,
    ShadowClassifier,
    ShadowComparisonBuilder,
    ShadowCoverageGate,
)
from runtime.errors import RuntimeValidationError
from tests.test_formal_context_builder import _prepare_project


PROJECT_PROFILES = {
    "simple_project": ("main.py",),
    "bookkeeping_app": (
        "ledger.py",
        "invoices.py",
        "reconciliation.py",
    ),
    "cross_file_project": (
        "domain/models.py",
        "services/reporting.py",
        "api/routes.py",
    ),
    "high_complexity_business": (
        "tenancy/policy.py",
        "accounts/ledger.py",
        "payments/reconciliation.py",
        "compliance/audit.py",
        "workflows/approval.py",
        "integrations/settlement.py",
    ),
}

ROLE_STATES = {
    "planner": ("PLANNING", "planner"),
    "generator": ("IMPLEMENTING", "generator"),
    "evaluator": ("EVALUATING", "evaluator"),
}


def _role_references(profile: str, role: str) -> tuple[str, ...]:
    """按真实角色路径权限选择同一项目画像的合法来源。"""

    prefixes = {
        "planner": "memory/requirements",
        "generator": "code",
        "evaluator": "evaluation/evidence",
    }
    prefix = prefixes[role]
    return tuple(f"{prefix}/{reference}" for reference in PROJECT_PROFILES[profile])


def _unit(
    unit_id: str,
    role: str,
    revision: int,
    *,
    context_class: str,
    authority: str = "RUNTIME_EVIDENCE",
    size: int = 128,
) -> ContextUnit:
    source_ref = f"runtime-evidence:{unit_id}"
    source_hash = hashlib.sha256(
        f"{role}:{revision}:{unit_id}".encode("utf-8")
    ).hexdigest()
    return ContextUnit(
        id=unit_id,
        context_class=context_class,
        authority=authority,
        source_ref=source_ref,
        source_hash=source_hash,
        exact_locator=source_ref,
        project_revision=revision,
        role_scope=role,
        reason="Post-Verification 合法项目权威来源",
        dependency_path=(unit_id,),
        delivery_mode="REFERENCE",
        size=size,
    )


def _semantic_for(
    role: str,
    revision: int,
    profile: str,
    optional_ids: tuple[str, ...],
) -> tuple[ContextSemanticModel, tuple[ContextUnit, ...], tuple[ContextUnit, ...]]:
    mandatory_ids = (
        "REQ-001",
        "AC-001",
        "PLAN-001",
        "APPROVAL-SCOPE-001",
        "SECURITY-001",
        "PRIVACY-001",
        "DATA-LOCALITY-001",
    )
    mandatory = tuple(
        _unit(
            item,
            role,
            revision,
            context_class="mandatory",
            authority=("APPROVED_PLAN" if item.startswith(("PLAN", "APPROVAL")) else "RUNTIME_EVIDENCE"),
            size=160,
        )
        for item in mandatory_ids
    )
    optional = tuple(
        _unit(item, role, revision, context_class="task_relevant", size=96)
        for item in optional_ids
    )
    constraints = {
        "global_constraints": ("SECURITY-001", "PRIVACY-001"),
        "task_constraints": ("AC-001",),
        "approval_constraints": ("APPROVAL-SCOPE-001",),
        "safety_constraints": ("DATA-LOCALITY-001",),
    }
    classes = {
        "mandatory": mandatory,
        "task_relevant": optional,
        "on_demand": (),
        "omitted": (),
        "unknown": (),
    }
    semantic_payload = f"{profile}:{role}:{revision}:{optional_ids}"
    semantic = ContextSemanticModel(
        task_identity=f"{profile}:{role}:post-verification",
        role=role,
        project_revision=revision,
        dependency_roots={
            "requirements": ("REQ-001",),
            "acceptance_criteria": ("AC-001",),
            "plan_tasks": ("PLAN-001",),
        },
        constraints=constraints,
        context_classes=classes,
        model_hash=hashlib.sha256(semantic_payload.encode("utf-8")).hexdigest(),
    )
    current = mandatory + optional
    candidate = mandatory
    return semantic, current, candidate


class F14CPostVerificationTests(unittest.TestCase):
    """验证合法项目不会被 C4 错误阻止，并区分两类失败语义。"""

    def test_legal_project_matrix_has_zero_false_blocks(self) -> None:
        matrix: list[dict[str, object]] = []
        false_block_count = 0
        false_omission_count = 0
        false_mandatory_count = 0

        for profile, references in PROJECT_PROFILES.items():
            for role, (status, next_role) in ROLE_STATES.items():
                with self.subTest(profile=profile, role=role):
                    role_references = _role_references(profile, role)
                    with tempfile.TemporaryDirectory(
                        prefix=f"test_f14_c_smoke_{profile}_{role}_"
                    ) as directory:
                        root = Path(directory)
                        store, session_id, run_id, project_root = _prepare_project(
                            root, status, next_role
                        )
                        for reference in role_references:
                            target = project_root / reference
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(
                                f"# {profile} {role} legal fixture\n", encoding="utf-8"
                            )
                        request = ContextBuildRequest(
                            session_id,
                            run_id,
                            role,
                            role_references,
                            task_identity=f"{profile}:{role}:post-verification",
                        )
                        current_context = ContextBuilder(store).build(request)
                        semantic, current_units, candidate_units = _semantic_for(
                            role,
                            current_context.project_revision,
                            profile,
                            tuple(f"FILE:{reference}" for reference in role_references),
                        )
                        classification = ShadowClassifier().classify(
                            semantic, current_units
                        )
                        comparison = ShadowComparisonBuilder().compare(
                            classification, current_units, candidate_units
                        )
                        seen: list[object] = []
                        decision, returned_context = EnforcedCoverageGate().invoke_with_fallback(
                            current_context,
                            lambda comparison=comparison: comparison,
                            lambda context: seen.append(context) or context,
                        )
                        expected_mandatory = set(classification.mandatory_ids)
                        optional_ids = {
                            unit.id
                            for unit in current_units
                            if unit.context_class != "mandatory"
                        }
                        false_omission_count += len(
                            expected_mandatory - {unit.id for unit in candidate_units}
                        )
                        false_mandatory_count += len(
                            expected_mandatory & optional_ids
                        )
                        false_block_count += decision.result != "ALLOW"
                        matrix.append(
                            {
                                "profile": profile,
                                "role": role,
                                "decision": decision.result,
                                "context_mode": decision.context_mode,
                                "mandatory_total": comparison.mandatory_total,
                                "mandatory_covered": comparison.mandatory_covered,
                                "false_omission": 0,
                                "false_mandatory": len(expected_mandatory & optional_ids),
                                "returned_f13_context": returned_context is current_context,
                                "model_received_f13": seen == [current_context],
                            }
                        )
                        self.assertEqual(decision.result, "ALLOW")
                        self.assertEqual(decision.context_mode, "F13_FULL")
                        self.assertIs(returned_context, current_context)
                        self.assertEqual(seen, [current_context])
                        self.assertFalse(expected_mandatory & optional_ids)

        self.assertEqual(len(matrix), 12)
        self.assertEqual(false_block_count, 0)
        self.assertEqual(false_omission_count, 0)
        self.assertEqual(false_mandatory_count, 0)
        print(
            "F14_C_LEGAL_SMOKE_MATRIX "
            + json.dumps(matrix, ensure_ascii=False, sort_keys=True)
        )
        print(
            "F14_C_ERROR_RATES "
            + json.dumps(
                {
                    "legal_cases": 12,
                    "false_block": false_block_count,
                    "false_omission": false_omission_count,
                    "false_mandatory": false_mandatory_count,
                    "critical_false_omission": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    def test_unavailable_coverage_falls_back_but_authoritative_incomplete_blocks(self) -> None:
        current_context = {"sources": ["f13-full-safe-context"]}
        gate = EnforcedCoverageGate()
        unavailable_components = (
            "ArtifactIndex",
            "DependencyGraph",
            "ShadowClassifier",
            "CandidateOptimizer",
        )
        for component in unavailable_components:
            with self.subTest(component=component):
                seen: list[object] = []

                def unavailable(component: str = component):
                    raise RuntimeError(f"{component} unavailable")

                decision, result = gate.invoke_with_fallback(
                    current_context,
                    unavailable,
                    lambda context: seen.append(context) or context,
                )
                self.assertEqual(decision.result, "FALLBACK_F13")
                self.assertTrue(decision.fallback_to_f13)
                self.assertIs(result, current_context)
                self.assertEqual(seen, [current_context])

        semantic, current_units, _ = _semantic_for(
            "generator", 4, "authoritative-incomplete", ()
        )
        incomplete_current = tuple(
            unit for unit in current_units if unit.id != "SECURITY-001"
        )
        classification = ShadowClassifier().classify(semantic, incomplete_current)
        comparison = ShadowComparisonBuilder().compare(
            classification, incomplete_current, incomplete_current
        )
        shadow_result = ShadowCoverageGate().evaluate(comparison)
        self.assertEqual(shadow_result.result, "FAIL")
        self.assertIn("mandatory_missing", shadow_result.reasons)
        seen: list[object] = []
        with self.assertRaisesRegex(
            RuntimeValidationError, "CONTEXT_COVERAGE_BLOCKED"
        ):
            gate.invoke_with_fallback(
                current_context,
                lambda comparison=comparison: comparison,
                lambda context: seen.append(context) or "must-not-call",
            )
        self.assertEqual(seen, [])
