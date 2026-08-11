"""RA7-B Acquisition Core 与 Runtime Safety 定向契约测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from runtime.execution.path_policy import ExecutionPathPolicy, PathAccessDenied
from runtime.reference_analysis.acquisition import (
    AcquisitionManifestStore,
    AcquisitionRequest,
    AcquisitionStatus,
    AcquisitionProviderRegistry,
    BrowserIsolationContract,
    LocalImageAcquisitionProvider,
    ProviderAvailability,
    ProviderCapability,
    deny_credential_access,
    deny_download,
    normalize_public_url,
    validate_redirect_chain,
    validate_resolved_addresses,
)
from runtime.reference_analysis.errors import ReferenceAnalysisError, ReferenceAnalysisCrash
from tests.test_reference_analysis_r2 import _make_project, _module


def _request(root: Path, *, locator: dict[str, object] | None = None, version: int = 1) -> AcquisitionRequest:
    return AcquisitionRequest(
        reference_id="REF-001",
        source_type="image",
        locator=locator or {"artifact_ref": "artifacts/references/input.png"},
        context={"type": "project", "project_id": "test-ra7b", "change_request_id": None},
        requested_scope={"visual_style": "include"},
        snapshot_version=version,
    )


def _png(width: int = 2, height: int = 3) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 24
    )


def test_image_provider_collects_safe_metadata_and_hash(tmp_path: Path) -> None:
    image = tmp_path / "artifacts" / "references" / "input.png"
    image.parent.mkdir(parents=True)
    raw = _png()
    image.write_bytes(raw)
    provider = LocalImageAcquisitionProvider()
    output = provider.acquire(
        _request(tmp_path, locator={"artifact_ref": "artifacts/references/input.png", "content_hash": hashlib.sha256(raw).hexdigest()}),
        root=tmp_path,
        path_policy=ExecutionPathPolicy(),
    )
    artifact = output.artifacts[0]
    assert artifact.artifact_ref == "artifacts/references/input.png"
    assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.metadata["width"] == 2
    assert artifact.metadata["height"] == 3
    assert artifact.metadata["pixel_count"] == 6
    assert output.metadata["trust_level"] == "untrusted"


def test_image_provider_rejects_escape_format_and_pixel_budget(tmp_path: Path) -> None:
    image = tmp_path / "artifacts" / "references" / "input.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_png(20, 20))
    policy = ExecutionPathPolicy()
    with pytest.raises(PathAccessDenied):
        LocalImageAcquisitionProvider().acquire(
            _request(tmp_path, locator={"artifact_ref": "../outside.png"}),
            root=tmp_path,
            path_policy=policy,
        )
    with pytest.raises(ReferenceAnalysisError) as exc:
        LocalImageAcquisitionProvider(max_pixels=10).acquire(
            _request(tmp_path), root=tmp_path, path_policy=policy
        )
    assert exc.value.code == "IMAGE_PIXEL_BUDGET_EXCEEDED"


def test_provider_registry_distinguishes_availability() -> None:
    class DeferredProvider:
        capability = ProviderCapability(
            provider_id="deferred-web",
            version=1,
            source_types=("web_page",),
            availability=ProviderAvailability.UNAVAILABLE,
        )

    registry = AcquisitionProviderRegistry((DeferredProvider(),))
    assert registry.capabilities()[0].availability is ProviderAvailability.UNAVAILABLE
    with pytest.raises(ReferenceAnalysisError) as exc:
        registry.for_source("web_page")
    assert exc.value.code == "ACQUISITION_PROVIDER_UNAVAILABLE"


def test_manifest_lifecycle_retry_refresh_and_idempotency(tmp_path: Path) -> None:
    store = AcquisitionManifestStore(tmp_path)
    capability = LocalImageAcquisitionProvider().capability
    request = _request(tmp_path)
    first = store.begin(request, capability)
    assert first.status is AcquisitionStatus.REQUESTED
    started = store.transition(first, AcquisitionStatus.STARTED)
    failed = store.transition(started, AcquisitionStatus.FAILED, error_code="TEST_FAILURE")
    retry = store.retry(failed)
    assert retry.acquisition_id == first.acquisition_id
    assert retry.attempt == 2
    completed = store.transition(
        store.transition(retry, AcquisitionStatus.STARTED),
        AcquisitionStatus.SUCCEEDED,
        artifact_refs=("artifacts/references/input.png",),
    )
    assert store.begin(request, capability).acquisition_id == completed.acquisition_id
    refreshed = store.refresh(request, completed, capability)
    assert refreshed.acquisition_id != completed.acquisition_id
    assert refreshed.snapshot_version == 2
    assert refreshed.refresh_of == completed.acquisition_id
    assert len(list((tmp_path / "artifacts" / "references" / "acquisitions").glob("manifest-*.yaml"))) == 7


def test_started_manifest_is_marked_unknown_after_crash(tmp_path: Path) -> None:
    store = AcquisitionManifestStore(tmp_path)
    manifest = store.begin(_request(tmp_path), LocalImageAcquisitionProvider().capability)
    store.transition(manifest, AcquisitionStatus.STARTED)
    recovered = store.recover_started()
    assert len(recovered) == 1
    assert recovered[0].status is AcquisitionStatus.UNKNOWN_AFTER_CRASH
    assert store.recover_started() == ()


def test_url_ssrf_redirect_download_and_credential_guards() -> None:
    canonical, addresses = normalize_public_url("https://example.com/path")
    assert canonical == "https://example.com/path"
    assert addresses == ()
    with pytest.raises(ReferenceAnalysisError) as exc:
        normalize_public_url("https://127.0.0.1/private")
    assert exc.value.code == "WEB_PRIVATE_ADDRESS_REJECTED"

    def private_resolver(*_args: object, **_kwargs: object) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        return [(None, None, None, None, ("10.0.0.2", 0))]

    with pytest.raises(ReferenceAnalysisError) as exc:
        normalize_public_url("https://example.com", resolver=private_resolver)
    assert exc.value.code == "WEB_PRIVATE_ADDRESS_REJECTED"
    with pytest.raises(ReferenceAnalysisError):
        validate_redirect_chain(["https://example.com", "http://example.com"])
    with pytest.raises(ReferenceAnalysisError) as exc:
        validate_resolved_addresses(["169.254.169.254"])
    assert exc.value.code == "WEB_PRIVATE_ADDRESS_REJECTED"
    with pytest.raises(ReferenceAnalysisError):
        deny_download()
    with pytest.raises(ReferenceAnalysisError):
        deny_credential_access()


def test_browser_isolation_contract_is_fail_closed() -> None:
    BrowserIsolationContract().validate()
    with pytest.raises(ReferenceAnalysisError) as exc:
        BrowserIsolationContract(host_cookies=True).validate()
    assert exc.value.code == "BROWSER_ISOLATION_PROFILE_INVALID"


def test_module_image_acquisition_uses_manifest_and_recovers(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    image = root / "artifacts" / "references" / "input.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_png())
    module = _module(root)
    source = {
        "reference_id": "REF-001",
        "source_type": "image",
        "source": {"artifact_ref": "artifacts/references/input.png"},
    }
    with pytest.raises(ReferenceAnalysisCrash):
        module.acquire_source(source, fail_at="after_start")
    assert len(module.acquisition_manifests.recover_started()) == 1
    recovered, output = module.acquire_source(source)
    repeated, repeated_output = module.acquire_source(source)
    assert recovered.acquisition_id == repeated.acquisition_id
    assert output.artifacts[0].sha256 == repeated_output.artifacts[0].sha256
    assert recovered.status is AcquisitionStatus.SUCCEEDED
