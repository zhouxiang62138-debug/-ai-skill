"""RA11 Recovery 的附件绑定与 F12 浏览器接线测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from runtime.reference_analysis import (
    AttachmentBindingStore,
    F12ReferenceBrowserRuntime,
    HostAttachment,
    ProviderAvailability,
)
from runtime.reference_analysis.errors import ReferenceAnalysisError
from runtime.errors import RuntimeValidationError


def _root_hash(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _resolver(host: str, *_args: object, **_kwargs: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    assert host == "example.com"
    return [(0, 0, 0, "", ("93.184.216.34", 443))]


def test_host_attachment_binding_is_hash_bound_and_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    host_file = tmp_path / "host" / "dashboard.png"
    host_file.parent.mkdir()
    host_file.write_bytes(b"\x89PNG\r\n\x1a\nreal-host-materialized-image")
    digest = hashlib.sha256(host_file.read_bytes()).hexdigest()
    attachment = HostAttachment(
        attachment_id="ATT-001",
        project_id="test_project",
        project_root_hash=_root_hash(project),
        source_path=host_file,
        filename="dashboard.png",
        media_type="image/png",
        expected_sha256=digest,
    )

    store = AttachmentBindingStore(project)
    first = store.bind(attachment, reference_id="REF-001")
    second = store.bind(attachment, reference_id="REF-001")

    assert first.binding["binding_id"] == second.binding["binding_id"]
    assert first.evidence["evidence_id"] == second.evidence["evidence_id"]
    artifact = project / str(first.binding["artifact_ref"])
    assert artifact.is_file()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
    assert first.evidence["artifact_ref"] == first.binding["artifact_ref"]


def test_attachment_binding_distinguishes_content_and_rejects_other_project(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    host_file = tmp_path / "host" / "same-name.bin"
    host_file.parent.mkdir()
    host_file.write_bytes(b"first-content")
    attachment = HostAttachment(
        attachment_id="ATT-002",
        project_id="project_a",
        project_root_hash=_root_hash(project_a),
        source_path=host_file,
        filename="same-name.bin",
        media_type="application/octet-stream",
    )
    AttachmentBindingStore(project_a).bind(attachment, reference_id="REF-001")

    with pytest.raises(ReferenceAnalysisError, match="ATTACHMENT_PROJECT_ROOT_MISMATCH"):
        AttachmentBindingStore(project_b).bind(attachment, reference_id="REF-001")
    with pytest.raises(ReferenceAnalysisError, match="ATTACHMENT_PROJECT_BINDING_INVALID"):
        AttachmentBindingStore(project_a).bind(
            attachment, reference_id="REF-001", project_id="project_b"
        )

    host_file.write_bytes(b"second-content")
    changed = HostAttachment(
        attachment_id="ATT-003",
        project_id="project_a",
        project_root_hash=_root_hash(project_a),
        source_path=host_file,
        filename="same-name.bin",
        media_type="application/octet-stream",
    )
    result = AttachmentBindingStore(project_a).bind(changed, reference_id="REF-001")
    assert result.binding["sha256"] != attachment.expected_sha256


def test_f12_browser_runtime_uses_existing_policy_and_fails_closed_without_playwright() -> None:
    runtime = F12ReferenceBrowserRuntime(resolver=_resolver)
    assert runtime.resolve("example.com") == ("93.184.216.34",)
    runtime.authorize_url("https://example.com/")
    with pytest.raises(RuntimeValidationError):
        runtime.authorize_url("https://api.github.com/")
    assert runtime.provider().capability.availability is ProviderAvailability.BLOCKED_BY_ENVIRONMENT


def test_f12_browser_runtime_requires_host_resolver() -> None:
    with pytest.raises(ReferenceAnalysisError, match="BROWSER_F12_RESOLVER_REQUIRED"):
        F12ReferenceBrowserRuntime(resolver=None)

