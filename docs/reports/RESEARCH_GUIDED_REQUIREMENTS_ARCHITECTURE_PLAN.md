# Research-Guided Adaptive Requirement Discovery：架构与实施计划

> 适用范围：AI Development Team Skill 本体仓库  
> 基线：Phase 0 Architecture Audit  
> 计划性质：实现前的协议设计与最小变更路线  
> 核心 Agent：Planner、Generator、Evaluator（三者保持不变）

## 1. 第一性原则

需求发现要解决的不是“用户回答了多少问题”，而是“在进入 Planner 前，哪些会显著改变产品范围、核心流程、数据、架构、权限、外部依赖、安全/合规或不可逆技术决定的事实已经被处理”。

因此：

```text
内部分析可以深
外部提问必须少而渐进
推进门禁必须确定性
研究发现永远不是用户需求
创意永远不能污染事实层
```

每轮最多 1～3 个问题仍然保留；总轮数和总问题数由 Gap 是否消除决定，而不是由固定计数决定。

## 2. 目标流程

```text
User Request
  ↓
Initial Intent Analysis
  ↓
Research Necessity Gate
  ├─ not_required → Coverage Map
  ├─ optional     → 用户需求 + 可用研究，进入 Coverage Map
  └─ required     → Domain Research Module
                         ↓
                  Domain Map / Benchmark / Opportunity
                         ↓
                  Coverage Map
                         ↓
                  Gap Analysis
                         ↓
                  Question Prioritizer
                         ↓
                  1～3 个问题
                         ↓
                  用户回答并创建新快照
                         └─ 回到 Intent/Coverage/Gap
  ↓
Deterministic Requirement Sufficiency Gate
  ↓
PLANNING → Planner Product Discovery
  ↓
受控 Creative Opportunity Discovery
  ↓
现有 Design Exploration / Product Proposal / Approval 链
```

Research 与用户明确提供的 Reference 必须保持边界：

```text
User Reference → Reference Analysis → Reference Synthesis
System Domain Research → Domain Research → Research Summary
```

两者可共享来源、证据和溯源底层能力，但不能共享“用户已经要求”的语义。

## 3. 模块结构与 Agent 边界

### 3.1 仍只有三个 Agent

```text
Planner
Generator
Evaluator
```

First-Ask、Intent Analyzer、Research Gate、Research、Coverage、Gap、Question Prioritizer、Sufficiency Evaluator 和 Creative Governance 都是 Module/Service/Deterministic Runtime Capability，不登记为 `next_role`，不创建独立 Agent Thread。

### 3.2 建议模块

```text
first_ask_intake
├─ IntentAnalyzer
├─ ResearchNecessityGate
├─ CoverageBuilder
├─ GapAnalyzer
├─ QuestionPrioritizer
└─ SufficiencyGate

domain_research
├─ ResearchPlanBuilder
├─ SourceAcquisitionAdapter
├─ EvidenceNormalizer
├─ DomainMapBuilder
├─ BenchmarkAnalyzer
└─ ResearchStopEvaluator
```

`domain_research` 是受控 Module，不是 Agent。研究完成、失败或不可用后，Runtime 将控制权返回 `first_ask_intake`；只有 Sufficiency Gate 通过，才将生命周期投影为 `PLANNING`。

## 4. 追加式工件模型

详细内容进入项目目录，不进入 Skill 本体，也不进入 Runtime Event Payload。推荐使用相对指针写入 `project.yaml`，工件本身按版本追加。

```text
memory/
├── requirements/
│   ├── request-<nnn>.md
│   ├── interview-<nnn>.md
│   ├── requirements_v<nnn>.yaml
│   ├── intent-<nnn>.yaml
│   ├── coverage-<nnn>.yaml
│   ├── gaps-<nnn>.yaml
│   └── question-set-<nnn>.yaml
├── research/
│   └── domain/
│       └── round_<nnn>/
│           ├── research-plan.yaml
│           ├── sources.yaml
│           ├── evidence.yaml
│           ├── domain-map.yaml
│           ├── benchmark-analysis.md
│           ├── opportunity-map.yaml
│           └── research-summary.md
└── references/
    └── ... existing user-provided Reference Analysis artifacts ...
```

所有工件必须包含：

- `schema_version`、唯一 ID、`created_at`、`supersedes`。
- `source_refs`、`research_refs`、`interview_refs` 或 `evidence_refs`。
- 当前请求/项目 Context。
- 可重放所需的配置版本、运行指纹和输入快照 Hash。

Research 工件不得覆盖上一轮；刷新来源必须创建新 Acquisition Snapshot 和新 Research Round。

## 5. Initial Intent Analysis

Intent Analyzer 只抽取用户明确事实，并把内部推断放进候选层。最小模型：

```yaml
intent_analysis:
  project_category: {value: ..., epistemic_status: candidate}
  domain: {value: ..., epistemic_status: candidate}
  likely_product_type: {value: ..., epistemic_status: inference}
  target_problem: {value: ..., epistemic_status: fact_or_unknown}
  known_facts: []
  unknown_facts: []
  likely_complexity: simple | standard | deep
  domain_maturity: low | medium | high | unknown
  research_value: low | medium | high
  risk_level: low | medium | high | regulated
  possible_core_workflows: []
  possible_data_model: []
  possible_integrations: []
  possible_nonfunctional_requirements: []
  design_uncertainty: []
```

`possible_*` 只能是 `candidate`、`hypothesis` 或 `inference`，不能直接写入 `required_features`、`business_rules` 或其他正式 Requirement 字段。

## 6. Research Necessity Gate

Gate 使用确定性规则和显式理由，不依赖模型“感觉”。规则输入包括：

- 产品类别、领域、复杂度和成熟度。
- 行业规范/监管风险。
- 用户是否明确要求行业最佳实践或 Benchmark。
- 初始事实是否足够完整。
- 当前环境是否提供授权的 Web/Browser Research Adapter。

输出：

```yaml
research_requirement:
  decision: required | optional | not_required
  reasons: []
  target_domains: []
  expected_value: []
  budget_profile: simple | standard | deep
  privacy_redaction: applied | not_needed
  source_policy: tiered
```

推荐默认分类：

- `required`：专业业务系统、成熟产品类别、强行业规范/监管、用户明确要求最佳实践、复杂数据分析或高风险决策。
- `optional`：普通产品类别、领域成熟但用户已给出部分规范，研究可明显提升覆盖但不是继续工作的硬依赖。
- `not_required`：简单单用途页面、低复杂度 Demo、用户已给出完整可执行规范且明确无需 Benchmark。

法律、医疗、金融监管等若需要最新官方标准而当前环境无法访问，研究结果应为 `unavailable`/`blocked`，不得伪造完成；是否继续由既有 User Boundary 处理。

## 7. Domain Research 设计

### 7.1 来源层级

```text
Tier 1：官方产品文档、正式标准、监管/政府、官方 API/数据规范
Tier 2：成熟产品的结构、工作流、权限、报表、错误处理和交互模式
Tier 3：专业协会、论文、设计机构、行业研究和工程规范
Tier 4：社区抱怨、Feature Request、用户痛点
```

Tier 4 只能发现机会，不能单独证明行业事实。Tier 2 只抽象结构和模式，不复制品牌、文案、视觉资产或受保护的具体设计。

### 7.2 研究输出的证据类型

统一使用明确的认识论标签：

```text
FACT        有来源直接支持的事实
PATTERN     多来源重复出现的结构模式
OBSERVATION 对单个来源的观察
INFERENCE   基于证据的当前项目推断
OPPORTUNITY 可能改善用户目标的产品机会
IDEA        创意候选，不是事实
```

Reference Analysis 现有 `observed/inferred/unknown` 可以作为底层字段，但 Domain Research 必须额外表达 `PATTERN/OPPORTUNITY/IDEA` 的用途，避免把成熟产品功能自动升为 Requirement。

### 7.3 研究预算和停止条件

```yaml
research_budget:
  simple:
    max_queries: 4
    max_sources: 6
    max_same_domain_sources: 2
  standard:
    max_queries: 8
    max_sources: 12
    max_same_domain_sources: 3
  deep:
    max_queries: 12
    max_sources: 20
    max_same_domain_sources: 4
```

停止条件：主要领域问题已经覆盖、Tier 1/2 已足够支撑需求发现，或新增来源的信息增益低于阈值。查询必须使用抽象领域问题，不携带用户敏感信息、凭证、私有代码或未授权项目内容。

### 7.4 失败降级

```text
completed          有可追溯来源并通过验证
partially_completed 已完成部分研究，明确未覆盖项
unavailable        环境没有可调用的研究适配器
not_required       Gate 判定无需研究
blocked            研究是关键依赖但受到权限/合规/环境阻塞
```

`unavailable` 且非关键阻塞时可以使用用户需求和内部通用知识，但降低 confidence；绝不把内部记忆写成外部来源。

## 8. Requirement Coverage Map

Coverage Map 不是固定问卷，而是每个项目按目标领域选择需要评估的覆盖维度。基础维度包括：

```text
Product Intent, Target Users, Primary Goal, Primary Use Cases,
Platform, Core Workflows, Required Features, Optional/Deferred Features,
Data Model/Data Source, Persistence/Sync, Authentication/User Model,
Permissions/Roles, External Integrations, Import/Export, Business Rules,
Reporting/Analytics, Error/Exception Handling, Security/Privacy,
Performance/Scale, Offline/Connectivity, Compatibility,
Deployment Constraints, Regulatory/Domain Constraints,
Design Preferences, Undecided Product Decisions
```

每个维度的最小结构：

```yaml
coverage_item:
  key: permissions.roles
  value: null
  status: answered | assumed | undecided | not_applicable | unanswered | conflicting | requires_user_decision
  source_refs: []
  importance: critical | high | medium | low
  decision_impact: critical | high | medium | low
  architecture_impact: critical | high | medium | low
  workflow_impact: critical | high | medium | low
  safe_assumption_available: true | false
  research_refs: []
  last_question_key: null
```

`answered` 只代表用户明确回答；`assumed` 必须是低风险、可逆且有理由的默认；`undecided` 不能被模型静默填成答案。

## 9. Gap Analysis 与 Question Prioritization

Gap Analyzer 根据 Coverage Map 计算：

- `critical_unanswered`：会阻止正确规划。
- `critical_conflicting`：来源冲突且无安全取舍。
- `requires_user_decision`：无安全默认且影响显著。
- `high_impact_unanswered`：会明显改变范围、流程、数据或架构。
- `safe_assumption_candidates`：可逆低风险项。
- `visual_undecided`：交给 Design Exploration，不继续采访。

问题排序采用确定性分桶，不强求浮点数学：

```text
CRITICAL > HIGH > MEDIUM > LOW
```

同一桶内按以下稳定键排序：

```text
decision_impact
→ architecture_impact
→ workflow_impact
→ irreversibility
→ dependency_count
→ risk
→ safe_assumption_available（无安全假设优先）
→ question_key（稳定 tie-breaker）
```

每轮只取前 1～3 个，并尽量属于同一决策主题，例如 User Model、Data Source 或 Core Workflow。用户回答后必须新建 Snapshot、Coverage 和 Gap 工件，重新计算，不能提前生成固定长问卷。

## 10. Deterministic Requirement Sufficiency Gate

Gate 不是“问题问完了”，而是检查：

```text
不存在 critical_unanswered
不存在 critical_conflicting
不存在 critical_requires_user_decision
核心目标、目标用户和至少一个核心场景已处理
平台已回答或有低风险、可逆假设
首版必须功能边界已明确或可审计地限定
适用的数据来源已回答，或明确无外部数据
硬限制/合规风险已回答，或明确没有已知限制
视觉 undecided 已路由 Design Exploration
```

允许继续的状态：`answered`、低风险 `assumed`、后续设计可处理的 `undecided`、`not_applicable`。不允许继续的状态：关键 `unanswered`、`conflicting`、`requires_user_decision`。

输出必须包含可复现原因：

```yaml
sufficiency_evaluation:
  decision: sufficient_for_planning | waiting_user | blocked
  blocking_items: []
  resolved_items: []
  safe_assumptions: []
  routed_to_design_exploration: []
  input_snapshot_hash: ...
  rules_version: ...
```

## 11. Planner Context 与产品发现

Planner 在 `PLANNING` 只读取：

```text
active_requirements
active_intent_analysis
active_research_summary
active_domain_map
active_benchmark_analysis
active_opportunity_map
active_coverage_map / sufficiency evaluation（用于解释来源，不用于改写需求）
```

Context Builder 通过 `config/context.yaml` 注册受控来源、优先级和预算；Generator 仍只读取 `approved_plan` 及其批准来源链，不能读取未批准 Opportunity 作为实施依据。

Planner 输出必须区分：

```text
Must Have       用户明确要求或已批准需求
Recommended     研究支持的建议，需要产品方案解释
Opportunity     创意候选，需用户/产品决策
Deferred/Rejected 未进入当前范围
```

研究和竞品功能不能直接写入 `required_features`，也不能绕过 Product Approval/Plan Approval。

## 12. Creativity 三段式隔离

```text
Strict Evidence
  用户原话、研究事实、约束、已批准 Requirement
        ↓
Divergent Creativity
  Conservative / Balanced / Innovative 候选机会和路线
        ↓
Strict Governance
  是否符合目标、是否扩大范围、是否进入 MVP、是否需要用户决定
```

不把 `temperature: 0.9` 等采样参数写成业务规则。若宿主支持参数映射，可由 Model Adapter 根据 `creativity_profile` 选择；不支持时通过 Prompt、Context 和 Task Definition 实现相同职责隔离。

## 13. Runtime/Context/CAS 最小接入

### 13.1 不新增正式工作流状态

优先保留 `INTAKE`/`WAITING_FOR_REQUIREMENTS`，通过 `active_module` 和小型投影字段表达研究阶段。只有在现有状态无法验证“研究进行中”和“等待用户”边界时，才考虑新增状态；新增状态必须同步 workflow、Schema、迁移、Runtime 和测试。

建议投影字段：

```text
requirements_discovery_status
research_status
intent_analysis_ref
research_requirement_ref
active_research_round
coverage_map_ref
gap_analysis_ref
question_set_ref
```

`status`、`next_role`、`active_module` 仍由 Runtime 派生；Module 只能通过有效 Lease、`expected_revision` 和 CAS 提交拥有的业务字段。

### 13.2 Recovery 与幂等

- 每个 Intent/Research/Coverage/Gap/Question 工件使用幂等键和输入 Hash。
- 重启时只补缺失工件，不覆盖已有非空工件。
- Acquisition 重试保留请求身份；刷新创建新 Snapshot。
- Runtime Event 只保存受控引用和 Hash，不保存凭据或长网页正文。
- WAITING 状态、BLOCKED、已验收和 ARCHIVED 不启动 Module/Role，按现有 F10 规则恢复。

## 14. 旧项目兼容与迁移

1. v3–v6 首次读取仍保持只读。
2. 缺少新 Discovery 字段时，读取层使用 `not_started`、`not_required`、空指针等安全默认，不回写。
3. 只有显式 `runtime-migrate` 或用户授权的项目迁移才补充 v7 投影字段。
4. 旧项目已有 `active_reference_synthesis` 时继续按用户 Reference 语义读取；不能把旧 Reference 当 Domain Research。
5. 新项目默认执行 Initial Intent Analysis；简单项目可以由 Gate 直接进入 Coverage/Question/Sufficiency，不强制联网。

## 15. 分阶段实施清单

### Phase 1 — Protocol / Schema Foundation

- 新增 Intent、Research Requirement、Research Round/Source/Evidence、Coverage、Gap、Question Set、Sufficiency、Opportunity Schema。
- 更新模板与命名规则，保留现有 Reference Schema。
- 更新字段所有权和 Context Source 类型，但不改变三 Agent。
- 为所有新状态/枚举添加 deterministic validators。

### Phase 2 — Research Necessity + Research

- 实现 Intent Analyzer 和 Research Gate。
- 实现 Domain Research Artifact Service，复用 Source Provenance/Acquisition/Evidence 基础能力。
- 接入授权 Web/Browser Adapter；没有能力时输出 `unavailable`，不得伪造。
- 实现预算、来源层级、停止条件、隐私脱敏和失败降级。

### Phase 3 — Adaptive Requirement Discovery

- 实现 Coverage Builder、Gap Analyzer、Question Prioritizer、动态多轮和 Sufficiency Gate。
- 把用户回答、研究候选和安全假设分别写入来源链。
- 保证视觉 `undecided` 路由 Design Exploration。
- 将推进权限收紧到 deterministic Gate。

### Phase 4 — Planner Integration

- 扩展 Planner Context 和 Prompt 的输入契约。
- 明确 Must Have/Recommended/Opportunity/Deferred。
- Planner 不写 Requirements Snapshot；发现基础事实缺失时仍返回 First-Ask。

### Phase 5 — Creative Opportunity Layer

- 增加 Strict Evidence、Divergent Creativity、Strict Governance 三个任务模式。
- 创意只产生追加式候选和决策记录，不直接改变事实层。

### Phase 6–7 — Runtime / Migration

- 只做必要的 Module Routing、CAS、Attestation、Resume、Recovery 和旧项目读取兼容。
- 不重写 F10，不改变 Product Approval、Plan Approval、Evaluator Independence。

### Phase 8–9 — Tests / Consistency Audit

- 增加 T1–T18 定向测试。
- 运行定向回归和全量 `python -m pytest -q`。
- 执行 `git diff --check` 和全局协议漂移搜索。
- 只有可复现必需证据齐全时才由 Evaluator/测试流程给出最终结论。

## 16. 验收矩阵

| 类别 | 必须证明 |
| --- | --- |
| 研究门禁 | 简单项目不强制研究，专业项目触发研究，研究失败不伪造完成 |
| 来源治理 | Tier、时间、来源、证据和 Hash 可追溯；低质量来源不能覆盖高权威来源 |
| 需求边界 | Research/Competitor Feature 不自动成为 Requirement |
| 多轮采访 | 每轮 1～3 个；回答后动态重算；不重复已解决问题 |
| 充分性 | Critical 未处理不得进入 Planner；Safe Assumption 有规则和理由 |
| 设计路由 | Visual undecided 进入现有 Design Exploration |
| 创意隔离 | Opportunity/Idea 不污染 Facts/Requirements |
| Agent 边界 | 只有 Planner、Generator、Evaluator；Research 是 Module |
| Runtime | Lease/CAS/Recovery/WAIT/Role Isolation 保持 |
| 审批 | Product Approval、Plan Approval、两阶段 Design Exploration 保持 |
| 兼容 | v3–v6 只读首次读取；新字段缺失可安全读取 |

## 17. 真实限制

- 当前仓库的网页适配器是合同校验，`fetch_enabled` 为 false；不能在没有 Host Adapter 的情况下声称已联网研究。
- 当前 `role_policies.yaml` 的公开 Web 服务白名单是受限的，新增研究域名必须经过 Capability/Network Policy 设计。
- 模型采样参数是否可控取决于宿主 Model Adapter；业务正确性不能依赖它。
- Browser Acquisition 在仓库运行路径标记为环境阻塞/延期；实际可用性必须以运行时授权和证据为准。
- 本次 Phase 0 测试尝试受到环境没有可用临时目录的限制，未据此伪造测试通过；生产代码尚未因该限制修改。

