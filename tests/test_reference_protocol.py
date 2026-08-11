"""Reference Analysis Protocol v1 的结构、来源链和回归测试。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from runtime.phase_runner import CORE_ROLES
from runtime.role_selector import select_role
from scripts.reference_protocol import (
    load_reference_config,
    validate_project_pointer,
    validate_reference_analysis,
    validate_reference_artifact_graph,
    validate_reference_evidence,
    validate_reference_finding,
    validate_reference_scope,
    validate_reference_source,
    validate_versioned_artifact_path,
)
from scripts.project_state import load_project_state, validate_project_state
from tests.runtime_test_support import make_runtime_project


REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAINS = list(load_reference_config()["analysis_domains"])


def _scope() -> dict[str, str]:
    return {domain: "unspecified" for domain in DOMAINS}


def _context() -> dict[str, str | None]:
    return {"type": "project", "project_id": "reference_protocol_test", "change_request_id": None}


def _source(source_type: str = "web_page") -> dict[str, object]:
    locator: dict[str, object] = {
        "uri": "https://example.test/reference",
        "artifact_ref": None,
        "text_ref": None,
        "identifier": None,
        "media_type": "text/html",
        "metadata": {},
    }
    if source_type == "image":
        locator["uri"] = None
        locator["artifact_ref"] = "artifacts/references/reference-001/source.png"
        locator["media_type"] = "image/png"
    if source_type == "text_description":
        locator["uri"] = None
        locator["identifier"] = "user-description-001"
        locator["media_type"] = "text/plain"
    return {
        "schema_version": 1,
        "reference_id": "REF-001",
        "source_type": source_type,
        "source": locator,
        "reference_mode": "inspiration",
        "scope_ref": "memory/references/reference-001/scope-001.yaml",
        "requested_scope": _scope(),
        "explicit_inclusions": [],
        "explicit_exclusions": [],
        "status": "registered",
        "created_at": "2026-08-09T00:00:00Z",
        "source_origin": {"type": "user_linked", "user_request_ref": "REQ-001"},
        "trust_level": "untrusted",
        "context": _context(),
        "supersedes": None,
    }


def _scope_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "scope_id": "REFSCP-001",
        "reference_id": "REF-001",
        "requested_scope": _scope(),
        "explicit_inclusions": [],
        "explicit_exclusions": [],
        "source_refs": ["REF-001"],
        "created_at": "2026-08-09T00:00:00Z",
        "context": _context(),
        "supersedes": None,
    }


def _evidence() -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_id": "REFEV-001",
        "reference_id": "REF-001",
        "evidence_type": "screenshot",
        "artifact_ref": "artifacts/references/reference-001/evidence/view.png",
        "integrity": {"algorithm": "sha256", "sha256": "0" * 64},
        "viewport": {"width": 1440, "height": 1000},
        "trust_level": "untrusted",
    }


def _finding() -> dict[str, object]:
    return {
        "schema_version": 1,
        "finding_id": "REFFND-001",
        "reference_id": "REF-001",
        "domain": "layout",
        "category": "spacing",
        "observation": {"value": "A visible spacing relationship", "measurement": None, "notes": None},
        "epistemic_status": "observed",
        "confidence": "medium",
        "evidence_refs": ["REFEV-001"],
        "user_scope_status": "unspecified",
        "inference_basis": [],
        "unknown_reason": None,
        "trust_level": "untrusted",
        "created_at": "2026-08-09T00:00:00Z",
    }


def _analysis() -> dict[str, object]:
    domains = {
        domain: {"status": "not_requested", "finding_ids": [], "evidence_refs": [], "note": None}
        for domain in DOMAINS
    }
    domains["layout"] = {
        "status": "analyzed",
        "finding_ids": ["REFFND-001"],
        "evidence_refs": ["REFEV-001"],
        "note": None,
    }
    return {
        "schema_version": 1,
        "analysis_id": "REFAN-001",
        "reference_id": "REF-001",
        "source_artifact_ref": "memory/references/reference-001/source-001.yaml",
        "scope_ref": "memory/references/reference-001/scope-001.yaml",
        "analysis_version": 1,
        "status": "completed",
        "domains": domains,
        "evidence_refs": ["REFEV-001"],
        "created_at": "2026-08-09T00:00:00Z",
        "supersedes": None,
        "context": _context(),
        "trust_level": "untrusted",
    }


def _synthesis() -> dict[str, object]:
    return {
        "schema_version": 1,
        "synthesis_id": "REFSYN-001",
        "source_references": ["REF-001"],
        "decisions": {
            "adopt": [
                {
                    "decision_id": "REFDEC-001",
                    "domain": "layout",
                    "source_findings": ["REFFND-001"],
                    "pattern": "spacing relationship",
                    "rationale": "It is within the requested scope.",
                    "decision_source": "reference_finding",
                    "user_scope_status": "unspecified",
                    "adaptation": None,
                }
            ],
            "adapt": [],
            "avoid": [],
        },
        "unknown": [],
        "priority_policy": {
            "explicit_user_requirements_override_reference": True,
            "approved_product_artifacts_override_reference": True,
            "reference_decision_is_not_requirement": True,
        },
        "created_at": "2026-08-09T00:00:00Z",
        "context": _context(),
        "trust_level": "untrusted",
        "supersedes": None,
    }


class ReferenceProtocolTests(unittest.TestCase):
    def test_all_versioned_schemas_are_json_and_have_v1_contract(self):
        schema_paths = sorted((REPO_ROOT / "config" / "schemas").glob("reference_*_v1.schema.json"))
        self.assertEqual(11, len(schema_paths))
        for path in schema_paths:
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, schema["properties"]["schema_version"]["const"])
            self.assertEqual("object", schema["type"])
            self.assertTrue(schema["required"])

    def test_first_active_source_types_register(self):
        self.assertEqual([], validate_reference_source(_source("web_page")))
        self.assertEqual([], validate_reference_source(_source("image")))
        self.assertEqual([], validate_reference_source(_source("text_description")))

    def test_unknown_source_type_is_rejected_by_configured_enum(self):
        record = _source()
        record["source_type"] = "pdf"
        self.assertTrue(validate_reference_source(record))

    def test_scope_is_tri_state_and_supports_explicit_exclusion(self):
        record = _source()
        record["requested_scope"] = _scope()
        record["requested_scope"]["visual_style"] = "exclude"
        record["explicit_exclusions"] = ["visual_style"]
        self.assertEqual([], validate_reference_source(record))
        record["requested_scope"]["layout"] = True
        self.assertTrue(validate_reference_source(record))

    def test_epistemic_status_requires_inference_or_unknown_reason(self):
        record = _finding()
        record["epistemic_status"] = "inferred"
        self.assertTrue(validate_reference_finding(record))
        record["inference_basis"] = ["Observed relationship in evidence REFEV-001"]
        self.assertEqual([], validate_reference_finding(record))
        record["epistemic_status"] = "unknown"
        record["inference_basis"] = []
        self.assertTrue(validate_reference_finding(record))
        record["unknown_reason"] = "The source does not expose this information."
        self.assertEqual([], validate_reference_finding(record))

    def test_estimates_are_distinct_from_measurements(self):
        record = _finding()
        record["observation"] = {
            "value": "estimated",
            "measurement": {
                "value_type": "estimated_range",
                "minimum": 8,
                "maximum": 12,
                "unit": "px",
            },
        }
        self.assertEqual([], validate_reference_finding(record))

    def test_evidence_requires_project_safe_path_and_sha256(self):
        self.assertEqual([], validate_reference_evidence(_evidence()))
        record = _evidence()
        record["artifact_ref"] = "C:/outside.png"
        record["integrity"]["sha256"] = "not-a-hash"
        self.assertTrue(validate_reference_evidence(record))

    def test_analysis_and_synthesis_trace_the_complete_graph(self):
        source, scope, analysis, finding, evidence, synthesis = (
            _source(), _scope_record(), _analysis(), _finding(), _evidence(), _synthesis()
        )
        self.assertEqual([], validate_reference_scope(scope))
        self.assertEqual([], validate_reference_analysis(analysis))
        self.assertEqual(
            [], validate_reference_artifact_graph(source, scope, analysis, [finding], [evidence], synthesis)
        )

    def test_dangling_finding_is_rejected(self):
        analysis = _analysis()
        analysis["domains"]["layout"]["finding_ids"] = ["REFFND-999"]
        errors = validate_reference_artifact_graph(
            _source(), _scope_record(), analysis, [_finding()], [_evidence()], _synthesis()
        )
        self.assertTrue(any("REFFND-999" in error for error in errors))

    def test_append_only_artifact_paths_are_versioned(self):
        self.assertEqual([], validate_versioned_artifact_path("memory/references/synthesis/reference-synthesis-002.yaml", "synthesis"))
        self.assertNotEqual("reference-synthesis-001.yaml", "reference-synthesis-002.yaml")
        self.assertTrue(validate_versioned_artifact_path("memory/references/synthesis/reference-synthesis.yaml", "synthesis"))

    def test_reference_analysis_routes_to_module_without_fourth_agent(self):
        selection = select_role({"status": "REFERENCE_ANALYSIS", "active_module": "reference_analysis", "next_role": None})
        self.assertEqual("MODULE", selection.kind)
        self.assertEqual("reference_analysis", selection.target)
        self.assertEqual({"planner", "generator", "evaluator"}, set(CORE_ROLES))

    def test_existing_v7_project_without_references_remains_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = make_runtime_project(directory)
            state = load_project_state(root / "project.yaml")
            self.assertEqual(7, state["schema_version"])
            self.assertNotIn("reference_status", state)
            self.assertEqual([], validate_project_state(state, root))

    def test_reference_analysis_state_projection_is_semantically_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = make_runtime_project(directory)
            state = load_project_state(root / "project.yaml")
            state.update(
                {
                    "status": "REFERENCE_ANALYSIS",
                    "next_role": None,
                    "active_module": "reference_analysis",
                    "reference_status": "provided",
                    "reference_analysis_status": "running",
                    "active_reference_synthesis": None,
                }
            )
            self.assertEqual([], validate_project_state(state, root))

    def test_untrusted_reference_cannot_express_authority(self):
        record = _source()
        record["runtime_authority"] = True
        self.assertTrue(validate_reference_source(record))
        self.assertEqual([], validate_project_pointer("memory/references/synthesis/reference-synthesis-001.yaml"))
        self.assertTrue(validate_project_pointer("artifacts/references/reference-001/evidence/view.png"))

    def test_change_request_context_is_explicitly_bound(self):
        record = _source()
        record["context"] = {
            "type": "change_request",
            "project_id": "reference_protocol_test",
            "change_request_id": "CR-0004",
        }
        self.assertEqual([], validate_reference_source(record))
        record["context"]["change_request_id"] = "CR-4"
        self.assertTrue(validate_reference_source(record))


if __name__ == "__main__":
    unittest.main()
