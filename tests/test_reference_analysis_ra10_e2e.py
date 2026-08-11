"""RA10 隔离 managed test project 的真实 Reference 链路。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from runtime.execution.path_policy import ExecutionPathPolicy
from runtime.reference_analysis.acquisition import (
    AcquisitionManifestStore,
    AcquisitionRequest,
    AcquisitionStatus,
    LocalImageAcquisitionProvider,
)
from runtime.reference_analysis.browser_acquisition import BrowserAcquisitionProvider
from runtime.reference_analysis.fusion import DeterministicObservation, FusionRequest, ReferenceFusionEngine
from runtime.reference_analysis.perception import CodexNativeMultimodalPerceptionProvider
from runtime.reference_analysis.adapters.text_description import TextDescriptionAdapter
from runtime.reference_analysis.registry import ReferenceRegistry
from scripts.project_state import write_project_state_atomic


def _state() -> dict[str, object]:
    return {
        "schema_version": 6,
        "project_id": "test_reference_e2e",
        "project_name": "Reference E2E fixture",
        "project_type": "application",
        "status": "ACCEPTED",
        "current_iteration": 0,
        "iteration_sequence": 1,
        "automatic_retry_allowed": False,
        "next_role": None,
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "proposal_status": "approved",
        "user_approval_status": "approved",
        "product_spec_status": "finalized",
        "plan_status": "approved",
        "plan_approval_status": "approved",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "iteration_metrics": None,
        "retry_history": [],
        "routing_disagreements": [],
        "plan_version": 1,
        "release_version": "1.0.0",
        "last_evaluation": None,
        "active_change_request": None,
        "change_cycle": 0,
        "change_context": None,
    }


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00\x00\x00\x02\x00\x00\x00\x02" + b"\x08\x06\x00\x00\x00" + b"\x00" * 24


def test_real_reference_paths_run_in_isolated_test_project_and_archive_report(tmp_path: Path) -> None:
    root = tmp_path / "test_reference_e2e"
    root.mkdir()
    write_project_state_atomic(root / "project.yaml", _state())
    (root / "code").mkdir()
    (root / "code" / "app.py").write_text("# unaffected\n", encoding="utf-8")
    (root / "artifacts" / "references").mkdir(parents=True)
    text_path = root / "artifacts" / "references" / "reference.txt"
    text_path.write_text("compact professional layout with a light theme", encoding="utf-8")

    config = ReferenceRegistry().config
    text_adapter = TextDescriptionAdapter(config=config)
    text_source = {
        "reference_id": "REF-001",
        "source_type": "text_description",
        "source": {
            "text_ref": "artifacts/references/reference.txt",
            "content_hash": hashlib.sha256(text_path.read_bytes()).hexdigest(),
        },
        "scope_ref": "memory/references/reference-001/scope-001.yaml",
        "context": {"type": "project", "project_id": "test_reference_e2e", "change_request_id": None},
    }
    normalized = text_adapter.normalize(text_source, root=root, path_policy=ExecutionPathPolicy())
    assert normalized.content and "professional" in normalized.content

    image_path = root / "artifacts" / "references" / "project-local.png"
    image_path.write_bytes(_png())
    request = AcquisitionRequest(
        reference_id="REF-002",
        source_type="image",
        locator={"artifact_ref": "artifacts/references/project-local.png"},
        context={"project_id": "test_reference_e2e"},
        requested_scope={"layout": "include", "visual_style": "include"},
    )
    image_provider = LocalImageAcquisitionProvider()
    manifests = AcquisitionManifestStore(root)
    manifest = manifests.begin(request, image_provider.capability)
    manifest = manifests.transition(manifest, AcquisitionStatus.STARTED)
    output = image_provider.acquire(request, root=root, path_policy=ExecutionPathPolicy())
    manifest = manifests.transition(manifest, AcquisitionStatus.SUCCEEDED, artifact_refs=tuple(item.artifact_ref for item in output.artifacts))
    assert manifest.status is AcquisitionStatus.SUCCEEDED
    assert output.artifacts[0].sha256 == hashlib.sha256(image_path.read_bytes()).hexdigest()

    native = CodexNativeMultimodalPerceptionProvider()
    assert native.availability.value == "AVAILABLE"
    assert native.capability["transport"] == "official_codex_sdk"
    assert native.perceive.__name__ == "perceive"

    browser = BrowserAcquisitionProvider()
    assert browser.capability.availability.value == "BLOCKED_BY_ENVIRONMENT"

    fusion = ReferenceFusionEngine().fuse(
        FusionRequest(
            reference_id="REF-002",
            requested_domains=("layout", "visual_style"),
            deterministic_observations=(
                DeterministicObservation(
                    source_ref="DOM-001",
                    reference_id="REF-002",
                    domain="layout",
                    category="viewport",
                    value="desktop",
                    evidence_refs=("REFEV-001",),
                ),
            ),
            visual_run_status="UNAVAILABLE",
        ),
        fusion_id="FUS-000010",
    )
    assert fusion.binding_status == "BLOCKED"
    assert "VISUAL_PERCEPTION_UNAVAILABLE" in fusion.limitations

    assert not (root / "memory" / "requirements").exists()
    assert (root / "code" / "app.py").read_text(encoding="utf-8") == "# unaffected\n"
    report = root / "archive" / "TEST_REPORT.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# RA10 Reference E2E Test Report\n\n"
        "- 项目：test_reference_e2e\n"
        "- Text Reference：通过\n"
        "- Project-local Image Acquisition：通过\n"
        "- User-attached/Native Perception：UNAVAILABLE，未伪造 PASS\n"
        "- Public Web Browser：BLOCKED_BY_ENVIRONMENT，未伪造抓取\n"
        "- Multi-reference Fusion：BLOCKED on unavailable visual capability\n",
        encoding="utf-8",
    )
    assert report.is_file()
