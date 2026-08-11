"""需求发现的确定性分析服务。

这些函数只负责把已有输入转换成可审计的候选、覆盖、缺口、问题和门禁结果，
不会把研究发现自动升级成用户 Requirement，也不会选择 Planner/G​enerator/Evaluator。
"""

from .core import (
    analyze_gaps,
    analyze_initial_intent,
    build_coverage_map,
    evaluate_research_necessity,
    evaluate_sufficiency,
    prioritize_questions,
)
from .research import (
    DomainResearchAdapter,
    DomainResearchModule,
    ResearchRunResult,
    ResearchUnavailable,
    UnavailableResearchAdapter,
    build_research_queries,
)
from .engine import (
    apply_question_answers,
    empty_requirements_snapshot,
    run_discovery_cycle,
)
from .artifacts import DiscoveryArtifactStore
from .opportunity import build_opportunity_map

__all__ = [
    "analyze_gaps",
    "analyze_initial_intent",
    "build_coverage_map",
    "evaluate_research_necessity",
    "evaluate_sufficiency",
    "prioritize_questions",
    "DomainResearchAdapter",
    "DomainResearchModule",
    "ResearchRunResult",
    "ResearchUnavailable",
    "UnavailableResearchAdapter",
    "build_research_queries",
    "apply_question_answers",
    "empty_requirements_snapshot",
    "run_discovery_cycle",
    "DiscoveryArtifactStore",
    "build_opportunity_map",
]
