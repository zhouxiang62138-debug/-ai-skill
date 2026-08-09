"""Evaluator 使用的确定性 Browser Harness 与 Browser Broker。"""

from .adapter import BrowserAdapter, PlaywrightBrowserAdapter
from .broker import BrowserBroker
from .errors import (
    BrowserActionFailed,
    BrowserEnvironmentBlocked,
    BrowserError,
    BrowserPolicyError,
)
from .evidence import evaluate_browser_gate, load_scenario_manifest, merge_browser_evidence
from .harness import BrowserHarness
from .models import (
    BrowserProfile,
    BrowserRunRecord,
    BrowserScenario,
    BrowserScenarioManifest,
    BrowserStepRecord,
)
from .policy import BrowserPolicy

__all__ = [
    "BrowserActionFailed",
    "BrowserAdapter",
    "BrowserBroker",
    "BrowserEnvironmentBlocked",
    "BrowserError",
    "BrowserHarness",
    "BrowserPolicy",
    "BrowserPolicyError",
    "BrowserProfile",
    "BrowserRunRecord",
    "BrowserScenario",
    "BrowserScenarioManifest",
    "BrowserStepRecord",
    "PlaywrightBrowserAdapter",
    "evaluate_browser_gate",
    "load_scenario_manifest",
    "merge_browser_evidence",
]
