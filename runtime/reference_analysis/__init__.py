"""Reference Analysis Core Module。"""

from .module import ReferenceAnalysisModule

from .acquisition import (
    AcquisitionManifest,
    AcquisitionManifestStore,
    AcquisitionOutput,
    AcquisitionProviderRegistry,
    AcquisitionRequest,
    AcquisitionStatus,
    BrowserIsolationContract,
    LocalImageAcquisitionProvider,
    ProviderAvailability,
    ProviderCapability,
    validate_redirect_chain,
    validate_resolved_addresses,
)
from .perception import (
    CodexHostBridge,
    CodexNativeMultimodalPerceptionProvider,
    HostInvocationBridge,
    ImageEvidenceInput,
    PerceptionRequest,
    PerceptionRun,
    PerceptionRunStore,
)
from .browser_acquisition import (
    BrowserAcquisitionProvider,
    BrowserCaptureAdapter,
    BrowserViewport,
    PlaywrightReferenceBrowserAdapter,
)
from .attachment_binding import AttachmentBindingResult, AttachmentBindingStore, HostAttachment
from .f12_browser import F12ReferenceBrowserRuntime
from .fusion import DeterministicObservation, FusionRequest, FusionResult, ReferenceFusionEngine
from .visual_conformance import VisualComparisonRequest, VisualConformanceProvider, VisualConformanceRun

__all__ = [
    "AcquisitionManifest",
    "AcquisitionManifestStore",
    "AcquisitionOutput",
    "AcquisitionProviderRegistry",
    "AcquisitionRequest",
    "AcquisitionStatus",
    "BrowserIsolationContract",
    "BrowserAcquisitionProvider",
    "BrowserCaptureAdapter",
    "BrowserViewport",
    "PlaywrightReferenceBrowserAdapter",
    "AttachmentBindingResult",
    "AttachmentBindingStore",
    "HostAttachment",
    "F12ReferenceBrowserRuntime",
    "DeterministicObservation",
    "FusionRequest",
    "FusionResult",
    "CodexNativeMultimodalPerceptionProvider",
    "CodexHostBridge",
    "HostInvocationBridge",
    "ImageEvidenceInput",
    "LocalImageAcquisitionProvider",
    "ProviderAvailability",
    "ProviderCapability",
    "PerceptionRequest",
    "PerceptionRun",
    "PerceptionRunStore",
    "ReferenceAnalysisModule",
    "ReferenceFusionEngine",
    "VisualComparisonRequest",
    "VisualConformanceProvider",
    "VisualConformanceRun",
    "validate_redirect_chain",
    "validate_resolved_addresses",
]
