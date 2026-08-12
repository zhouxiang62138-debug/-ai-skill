"""配置驱动的 Reference Adapter 与 Domain Analyzer 注册表。"""

from __future__ import annotations

import importlib
from typing import Any

from scripts.reference_protocol import load_reference_config

from .errors import ReferenceAnalysisError


def _load_symbol(locator: str) -> Any:
    if not isinstance(locator, str) or ":" not in locator:
        raise ReferenceAnalysisError("REFERENCE_IMPLEMENTATION_INVALID")
    module_name, symbol_name = locator.split(":", 1)
    if not module_name or not symbol_name:
        raise ReferenceAnalysisError("REFERENCE_IMPLEMENTATION_INVALID")
    try:
        symbol = getattr(importlib.import_module(module_name), symbol_name)
    except (ImportError, AttributeError) as exc:
        raise ReferenceAnalysisError("REFERENCE_IMPLEMENTATION_LOAD_FAILED") from exc
    return symbol


class ReferenceRegistry:
    """所有 source_type 和 analyzer 均必须来自配置映射。"""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_reference_config()
        self._adapters: dict[str, Any] = {}
        for source_type, entry in (self.config.get("adapters") or {}).items():
            if not isinstance(entry, dict) or entry.get("enabled") is not True:
                continue
            cls = _load_symbol(entry.get("implementation"))
            self._adapters[str(source_type)] = cls(
                config=self.config, version=int(entry.get("version", 1))
            )
        self._analyzers: dict[str, tuple[Any, ...]] = {}
        for source_type, entries in (self.config.get("analyzers") or {}).items():
            if not isinstance(entries, list):
                raise ReferenceAnalysisError("REFERENCE_ANALYZER_CONFIG_INVALID")
            loaded = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ReferenceAnalysisError("REFERENCE_ANALYZER_CONFIG_INVALID")
                cls = _load_symbol(entry.get("implementation"))
                loaded.append(cls(config=self.config, version=int(entry.get("version", 1))))
            self._analyzers[str(source_type)] = tuple(loaded)

    def adapter(self, source_type: str) -> Any:
        adapter = self._adapters.get(source_type)
        if adapter is None:
            raise ReferenceAnalysisError("REFERENCE_ADAPTER_NOT_REGISTERED")
        return adapter

    def analyzer(self, source_type: str) -> Any:
        analyzers = self._analyzers.get(source_type, ())
        if not analyzers:
            raise ReferenceAnalysisError("REFERENCE_ANALYZER_NOT_REGISTERED")
        return analyzers[0]

    def supported_source_types(self) -> frozenset[str]:
        return frozenset(self._adapters)
