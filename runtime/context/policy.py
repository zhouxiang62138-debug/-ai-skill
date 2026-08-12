"""F13.1/F13.2 上下文资料与预算策略；路径权限继续由 F11/F12 强制。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from runtime.errors import RuntimeValidationError
from scripts.project_state import parse_project_yaml


_ROOT = Path(__file__).resolve().parents[2]
_CORE_ROLES = frozenset({"planner", "generator", "evaluator"})
_PRIORITIES = frozenset({"REQUIRED", "HIGH", "NORMAL", "REFERENCE_ONLY"})
_DELIVERY_MODES = frozenset({"INLINE", "REFERENCE"})
_SOURCE_TYPES = frozenset(
    {
        "project_state",
        "state_reference",
        "state_value",
        "reference_catalog",
        "design_reference_subset",
        "approved_reference_bindings",
        "reference_conformance_subset",
    }
)


@dataclass(frozen=True)
class ContextSourceRule:
    """配置声明的一条 Context 来源规则。"""

    source_type: str
    reference: str | None
    field: str | None
    reason: str
    priority: str = "NORMAL"
    delivery_mode: str = "INLINE"


@dataclass(frozen=True)
class ContextBudgetConfig:
    """每个 Role 的确定性字节预算，不伪装成精确模型 Token 数。"""

    max_context_bytes: int
    max_inline_bytes: int
    max_sources: int
    max_source_inline_bytes: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_context_bytes": self.max_context_bytes,
            "max_inline_bytes": self.max_inline_bytes,
            "max_sources": self.max_sources,
            "max_source_inline_bytes": self.max_source_inline_bytes,
        }


def _config_path(config_path: str | Path | None) -> Path:
    return Path(config_path) if config_path else _ROOT / "config" / "context.yaml"


class ContextPolicy:
    """从 context.yaml 读取 Role-specific Context 选择规则。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = _config_path(config_path)
        try:
            document = parse_project_yaml(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeValidationError("CONTEXT_POLICY_UNAVAILABLE") from exc
        independence_path = _ROOT / "config" / "evaluation_independence.yaml"
        try:
            independence = parse_project_yaml(independence_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_UNAVAILABLE") from exc
        context_independence = independence.get("context")
        if not isinstance(context_independence, dict):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        excluded_fields = context_independence.get("excluded_fields")
        excluded_prefixes = context_independence.get("excluded_reference_prefixes")
        excluded_labels = context_independence.get("excluded_source_labels")
        if not all(
            isinstance(value, list) and all(isinstance(item, str) and item for item in value)
            for value in (excluded_fields, excluded_prefixes, excluded_labels)
        ):
            raise RuntimeValidationError("EVALUATION_INDEPENDENCE_POLICY_INVALID")
        if document.get("version") != 1 or document.get("default") != "deny":
            raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
        raw_roles = document.get("roles")
        if not isinstance(raw_roles, dict) or set(raw_roles) != _CORE_ROLES:
            raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
        self._evaluator_excluded_fields = frozenset(excluded_fields)
        self._evaluator_excluded_reference_prefixes = tuple(excluded_prefixes)
        self._evaluator_excluded_source_labels = frozenset(excluded_labels)
        self._policy_hash = hashlib.sha256(
            json.dumps(
                {"context": document, "evaluation_independence": independence},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._roles: dict[str, tuple[ContextSourceRule, ...]] = {}
        self._budgets: dict[str, ContextBudgetConfig] = {}
        for role, raw_policy in raw_roles.items():
            if not isinstance(raw_policy, dict) or not isinstance(
                raw_policy.get("sources"), list
            ):
                raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
            rules: list[ContextSourceRule] = []
            for raw_rule in raw_policy["sources"]:
                if not isinstance(raw_rule, dict):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                source_type = raw_rule.get("source_type")
                reference = raw_rule.get("reference")
                field = raw_rule.get("field")
                reason = raw_rule.get("reason")
                priority = raw_rule.get("priority", "NORMAL")
                delivery_mode = raw_rule.get("delivery_mode", "INLINE")
                if not isinstance(source_type, str) or source_type not in _SOURCE_TYPES:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if reference is not None and not isinstance(reference, str):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if field is not None and not isinstance(field, str):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if not isinstance(reason, str) or not reason:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if priority not in _PRIORITIES or delivery_mode not in _DELIVERY_MODES:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if source_type == "project_state" and reference != "project.yaml":
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if source_type != "project_state" and not field:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                rules.append(
                    ContextSourceRule(
                        source_type,
                        reference,
                        field,
                        reason,
                        priority,
                        delivery_mode,
                    )
                )
            self._roles[role] = tuple(rules)
            budget_values = {
                key: raw_policy.get(key)
                for key in (
                    "max_context_bytes",
                    "max_inline_bytes",
                    "max_sources",
                    "max_source_inline_bytes",
                )
            }
            if not all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in budget_values.values()
            ):
                raise RuntimeValidationError("CONTEXT_BUDGET_CONFIG_INVALID")
            self._budgets[role] = ContextBudgetConfig(**budget_values)

        raw_modules = document.get("modules", {})
        if raw_modules is not None and not isinstance(raw_modules, dict):
            raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
        for module, raw_policy in (raw_modules or {}).items():
            if module in self._roles or not isinstance(raw_policy, dict) or not isinstance(raw_policy.get("sources"), list):
                raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
            rules: list[ContextSourceRule] = []
            for raw_rule in raw_policy["sources"]:
                if not isinstance(raw_rule, dict):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                source_type = raw_rule.get("source_type")
                reference = raw_rule.get("reference")
                field = raw_rule.get("field")
                reason = raw_rule.get("reason")
                priority = raw_rule.get("priority", "NORMAL")
                delivery_mode = raw_rule.get("delivery_mode", "INLINE")
                if source_type not in _SOURCE_TYPES:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if source_type == "project_state" and reference != "project.yaml":
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if source_type != "project_state" and not isinstance(field, str):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if reference is not None and not isinstance(reference, str):
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if not isinstance(reason, str) or not reason:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                if priority not in _PRIORITIES or delivery_mode not in _DELIVERY_MODES:
                    raise RuntimeValidationError("CONTEXT_POLICY_INVALID")
                rules.append(ContextSourceRule(source_type, reference, field, reason, priority, delivery_mode))
            self._roles[module] = tuple(rules)
            budget_values = {key: raw_policy.get(key) for key in ("max_context_bytes", "max_inline_bytes", "max_sources", "max_source_inline_bytes")}
            if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in budget_values.values()):
                raise RuntimeValidationError("CONTEXT_BUDGET_CONFIG_INVALID")
            self._budgets[module] = ContextBudgetConfig(**budget_values)

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(self._roles)

    @property
    def policy_hash(self) -> str:
        """返回包含角色来源、优先级和预算的稳定策略指纹。"""

        return self._policy_hash

    def rules_for(self, role: str) -> tuple[ContextSourceRule, ...]:
        if role not in self._roles:
            raise RuntimeValidationError("CONTEXT_UNKNOWN_ROLE")
        return self._roles[role]

    def budget_for(self, role: str) -> ContextBudgetConfig:
        if role not in self._budgets:
            raise RuntimeValidationError("CONTEXT_UNKNOWN_ROLE")
        return self._budgets[role]

    def evaluator_source_excluded(self, field: str | None, reference: str | None) -> bool:
        """判断来源是否属于 E1 明确排除的 Generator 叙事输入。"""

        normalized = (reference or "").replace("\\", "/").casefold()
        if any(
            normalized.startswith(prefix.casefold())
            for prefix in self._evaluator_excluded_reference_prefixes
        ):
            return True
        if field is not None and field in self._evaluator_excluded_fields:
            # 兼容历史状态字段：它可能指向事实性 handoff；只有指向响应/自评目录
            # 时才排除，避免把 Generator 的定位信息误当成完整聊天历史。
            factual_handoff = (
                normalized.startswith("memory/handoffs/")
                or "/handoffs/" in normalized
                or normalized.startswith("handoff-")
            )
            return not factual_handoff
        return False

    @property
    def evaluator_excluded_source_labels(self) -> frozenset[str]:
        return self._evaluator_excluded_source_labels

    def validate(self) -> dict[str, tuple[ContextSourceRule, ...]]:
        """返回只读策略快照，便于测试配置驱动而非散落硬编码。"""

        return dict(self._roles)
