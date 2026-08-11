# Research-Guided Adaptive Requirement Discovery：当前状态架构审计

> 阶段：Phase 0 — Architecture Audit  
> 审计对象：`ai-development-team-skill` 工作副本  
> 审计日期：2026-08-11  
> 状态：审计完成，尚未修改生产代码

## 1. 审计结论

当前 Skill 已经具备可靠的“角色隔离、文件驱动、追加式历史、F10 持久化运行时、Reference Analysis 和两阶段 Design Exploration”基础，但需求发现仍是“协议写得比较完整，确定性执行能力不足”的状态。

真正的缺口不是问题数量，而是没有一条可验证的需求发现闭环：

```text
初始意图
→ 研究门禁
→ 领域证据
→ 覆盖图
→ 缺口分析
→ 问题优先级
→ 用户回答
→ 重新计算
→ 充分性门禁
```

当前 Runtime 会根据 `project.yaml` 中已有的 `requirements_status` 路由，但不会独立计算这个状态是否真的成立。因此，后续升级必须把“分析、研究、覆盖、缺口、问题选择、充分性”做成可测试的 Module/Service 和追加式工件；不能只继续扩写 Prompt。

工作副本在审计开始时已有用户未提交修改，涉及 Design Exploration、Planner、Project State、Reference Analysis 回归测试等文件。本轮审计没有覆盖、回写或清理这些修改。

## 2. 对 Phase 0 八个问题的直接回答

| 问题 | 当前事实 | 结论 |
| --- | --- | --- |
| First-Ask 如何判断 `sufficient_for_planning`？ | `intake/first_ask.md` 规定按目标用户、核心目标、场景、平台、首版功能、数据来源和硬限制检查；但 `runtime/intake/first_ask.py` 没有实现该检查，只在 `_route()` 读取现有 `project.yaml.requirements_status`。 | 当前是 Prompt/模板约束，不是确定性 Sufficiency Service。 |
| 是否已有 Requirement Coverage？ | 只有 `requirements_snapshot.yaml` 的固定字段、`open_questions` 和 `completion_assessment.critical_fields_ready`；没有 Coverage Map、Gap Model、字段级影响元数据或可执行覆盖计算。 | 存在隐含字段清单，不存在正式 Coverage 能力。 |
| 是否有外部 Research/Reference Analysis？ | 有完整的 `runtime/reference_analysis/`：来源登记、Scope、Acquisition Manifest、Evidence、Finding、Synthesis、追加式存储与 Traceability。 | 可复用底层协议，但它面向“用户提供的 Reference”，不是主动领域研究。 |
| 当前联网/Browser 能力在哪里？ | F12 能力和 `runtime/reference_analysis/acquisition.py`、`browser_acquisition.py` 提供合同和生命周期；`config/reference_analysis.yaml` 明确 `web_acquisition.fetch_enabled: false`、`NETWORK_ACQUISITION_DEFERRED`，网页适配器只做 URL 合同校验。 | 当前 Skill 仓库不能把自己描述成已完成公共 Web Research；宿主 Browser/Web 能力需由授权适配器接入。 |
| Research 如何进入 Planner Context？ | `config/context.yaml` 已让 Planner读取 `active_requirements`、`active_reference_synthesis`；Context Builder 只读取状态指针和受控 Reference Catalog/Synthesis。 | 用户 Reference 已能进入 Planner；主动领域研究尚无指针、工件类型和 Context Source Rule。 |
| 哪些字段由 Runtime 派生？ | `config/role_policies.yaml` 的 `lifecycle_authority` 明确 `status`、`next_role`、`active_module`、`active_change_request` 由 Runtime CAS 管理；`role_selector.py` 按状态路由选择 Module 或 Agent。 | 新能力必须保持 `next_role` 只允许 Planner、Generator、Evaluator；Research 不能成为第四个 Agent。 |
| v7 如何约束 requirements？ | `project_v6.schema.json` 约束 `requirements_status` 枚举、`active_requirements` 指针等；v7 通过 Runtime Extension 继承 v6 并增加 Session/Control Plane/Revision。Schema 对需求快照内容不做 JSON Schema 校验，且 `additionalProperties: true`。 | v7 约束项目状态和路径，不能证明需求快照充分；需要新增独立需求发现 Schema/Deterministic Gate。 |
| 哪些测试必须兼容？ | Reference Analysis R2–RA11、F10 Runtime、Role Isolation、CAS/Lease/Recovery、Planner/Product/Plan Approval、双阶段 Design Exploration、Change Request 和 Evaluator Independence 都必须保持。 | 新功能必须以 Module、投影字段和 Context 扩展接入，不得重写 F10 或改变 Product/Plan Approval。 |

## 3. 当前实际执行链

### 3.1 First-Ask 文档协议

`intake/first_ask.md` 已经定义了不少正确规则：

- First-Ask 是 `active_module: first_ask_intake`，不是 Agent。
- 每轮最多 1～3 个问题。
- 用稳定 `question_key` 去重。
- 用户明确“不确定”时使用 `undecided`，视觉问题路由 Design Exploration。
- 原始请求、采访轮次和需求快照追加保存。
- Planner 对 `memory/requirements/` 只读。

但这些规则主要是给模型执行的说明，缺少一个能读取快照、计算 Gap、生成排序结果并拒绝非法推进的 Python 入口。

### 3.2 Runtime First-Ask 实现

`runtime/intake/first_ask.py` 的职责目前集中在：

1. 保守识别用户明确提供的 URL、图片路径和文字 Reference。
2. 通过 `ReferenceArtifactStore` 注册 Reference Source/Scope。
3. 将 Reference 指针写入需求快照。
4. 在需求状态已经是 `sufficient_for_planning` 时路由到 `REFERENCE_ANALYSIS` 或 `PLANNING`。
5. 通过 Orchestrator 的 Lease、CAS 和 Module Step 提交状态变更。

该实现没有：

- Initial Intent Analysis。
- Research Necessity Gate。
- Domain Research 生命周期。
- Requirement Coverage Map。
- Gap Analysis。
- Question Value/优先级计算。
- 用户回答后的字段级重新分析。
- 独立的 deterministic Sufficiency Evaluator。

因此，当前 `process_user_message()` 不是“需求采访引擎”，而是“Reference 注册与路由入口”。

## 4. 可复用能力盘点

### 4.1 Reference Analysis 可复用部分

可以直接复用或抽象复用：

- `ReferenceArtifactStore` 的追加式 YAML 工件写入、版本和路径保护。
- `reference_source_v1` 的来源定位、Source Origin、Context 和 `trust_level`。
- `reference_evidence_v1` 的 SHA-256 完整性、采集时间、定位器、Acquisition 绑定。
- `reference_finding_v1` 的 `observed / inferred / unknown` 和置信度。
- `reference_synthesis_v1` 的来源引用、冲突和“Reference decision is not requirement”政策。
- Acquisition Manifest 的 `REQUESTED → STARTED → SUCCEEDED/FAILED/TIMED_OUT/...` 生命周期。
- F12 的 Secret 检测、网络白名单、公开 URL 校验和 Browser 隔离合同。

不能直接复用为主动领域研究的部分：

- `reference_mode`、用户显式 Scope 和 Reference Design Domains。
- 当前 `web_page` 适配器的“只校验 URL、不直接获取”。
- 当前 `active_reference_synthesis` 的设计参考语义。
- 当前 `reference_analysis` 状态，它表达的是用户 Reference 的分析，不表达 Domain Research 是否已满足。

### 4.2 F10/F13 可复用部分

- Orchestrator 的 Module Step、Lease、CAS、Idempotency 和 Recovery。
- `role_selector.py` 对 Module/Role 的硬边界。
- `ContextBuilder` 的源选择、预算、Hash、Secret-Free 检查和 Role Isolation。
- `config/context.yaml` 的按角色来源策略。
- `config/role_policies.yaml` 的 Capability、Module Authorization、Field Ownership 和 Lifecycle Authority。
- 现有 `project.yaml` 单一状态投影与旧 v3–v6 只读迁移政策。

升级不应重复实现这些能力，也不应把研究过程写入 Skill 目录或把长网页原文塞入 Runtime Event Payload。

## 5. 当前真正缺口

| 缺口 | 现状 | 风险 |
| --- | --- | --- |
| 意图层 | 初始用户原话直接进入现有字段或 Planner 解释 | Domain Candidates 可能被误当成用户需求 |
| 研究门禁 | 没有 `research_requirement` 结构和确定性规则 | 简单项目被无差别联网，专业项目又可能不研究 |
| 研究模型 | Reference Schema 没有 `authority_level`、`retrieved_at`、研究问题和预算 | 无法区分用户 Reference 与系统发现的行业事实 |
| 覆盖图 | 固定字段列表代替产品定义维度 | 无法知道哪些高影响决策尚未处理 |
| 缺口分析 | 只有 `open_questions` 文本数组 | 无法重排，也无法证明回答解决了哪些缺口 |
| 问题选择 | Prompt 给出宽泛优先级，没有确定性排序键 | 容易从 unanswered 字段随便选三个 |
| 动态多轮 | 文档要求重新读取，但 Runtime 没有状态机/服务承接 | 可能机械重复或提前进入 Planner |
| 充分性 | `project.yaml` 的枚举值可被直接设置 | 关键 unknown 可能被模型自称“够了”绕过 |
| Planner Research Context | 只有 `active_reference_synthesis` | Domain Map、Benchmark、Opportunity 没有受控输入 |
| 创意隔离 | 现有 Reference synthesis 有 adopt/adapt/avoid，但无 Opportunity/Creative Governance | 推荐或创意可能污染正式 Requirement |
| 失败降级 | Reference Analysis 有 blocked/deferred，但没有 Domain Research unavailable/partial 语义 | 可能伪造“已研究”或把环境失败看成用户决策 |

## 6. 状态、权限和 v7 边界

### 6.1 建议保持的生命周期不变量

不增加第四个 Agent，不让 Research 写入 `next_role`。推荐让 `active_module` 表达当前受控能力，`next_role` 仍只表达核心 Agent：

```text
INTAKE / WAITING_FOR_REQUIREMENTS
  active_module = first_ask_intake 或 domain_research
  next_role = null

PLANNING
  active_module = null
  next_role = planner
```

研究完成后由 Runtime CAS 将控制权返回 First-Ask，只有 Sufficiency Gate 通过后才投影为 `PLANNING`。

### 6.2 字段归属

继续由 Runtime 独占：

```text
status
next_role
active_module
active_change_request
```

由 First-Ask/Research Module 通过受控 Module Step 写入：

```text
requirements_status
requirements_version
active_requirements
active_interview
intake_round
intent_analysis_ref
research_requirement_ref
research_status
active_research_round
coverage_map_ref
gap_analysis_ref
question_set_ref
```

这些字段只保存相对路径、状态投影和版本指针；详细研究与分析必须存为追加式项目工件。

### 6.3 现有 v7 的兼容结论

v7 的 `additionalProperties: true` 可以让旧项目读取未知字段，但这不等于可以随意写字段。新增字段仍必须同步：

- v7/迁移 Schema。
- `project_state.py` 的语义校验。
- `role_policies.yaml` 的所有权。
- `workflow.yaml` 的 Module 路由。
- Context 和 Path Policy。
- v3–v6 只读预览与 v7 显式迁移测试。

旧项目缺少这些字段时，必须按“无主动研究、从初始意图重新计算”的安全默认读取；不得回写旧项目，除非用户明确启动迁移。

## 7. 测试兼容基线

以下行为不可回归：

- `test_reference_analysis_r3.py`：用户 Reference 保守识别、登记、路由和重复消息幂等。
- `test_reference_protocol.py` 与 RA7–RA11：Source/Evidence/Finding/Synthesis、Acquisition、Browser、Security、Recovery 和 Conformance。
- `test_project_state.py`、Schema v3–v7 Preview/Migration：旧状态首次读取只读，v7 CAS 和路径校验保持。
- `test_orchestrator_role_selection.py`、`test_orchestrator_wait_states.py`：WAIT 状态不启动 Role，Module 不写入 `next_role`。
- `test_p0_workflow_cas_ownership.py`、Lease/Recovery/Event/Context 测试：生命周期与 Runtime 所有权不变。
- Planner/Approval/Design Exploration/Change Request/Evaluator Independence 测试：Product Approval、Plan Approval、双阶段设计和 Evaluator 独立性不改变。

新增 T1–T18 需求发现测试应是增量测试，不得用降低原门禁的方式通过。

## 8. 最小正确升级路径

1. Phase 1：新增独立的 Intent、Research Gate、Coverage、Gap、Question、Sufficiency 数据模型和模板；先不改变 Agent 体系。
2. Phase 2：实现确定性 Intent/Research Gate 和 Research Artifact Service。优先复用 Reference Provenance，不把用户 Reference 与系统 Domain Research 合并。
3. Phase 3：实现 Coverage、Gap、Question Prioritizer、动态多轮和 Sufficiency Gate；由 Runtime CAS 按 Module Step 提交投影。
4. Phase 4：扩展 Planner Context，使 Planner 读取 Active Requirements、Research Summary、Domain Map、Benchmark Patterns 和 Opportunity Candidates，但禁止把 Research 自动写成 Requirement。
5. Phase 5：加入 Strict Evidence → Divergent Creativity → Strict Governance 的 Opportunity 层，不修改事实层。
6. Phase 6–7：仅做必要 Runtime/Context/CAS/Recovery 接入和旧项目安全读取；不重构 F10。
7. Phase 8–9：定向测试、全量回归和全局协议一致性搜索。

详细目标设计见同目录的 `RESEARCH_GUIDED_REQUIREMENTS_ARCHITECTURE_PLAN.md`。

## 9. Phase 0 交付边界

本阶段只完成了源代码、配置、Schema、模板、Runtime、文档和测试的架构审计，并生成两份审计工件；没有修改 Production Code、没有改变工作流、没有创建测试项目、没有删除文件。

可进入 Phase 1 的原因是：核心架构分叉已经被收敛为“新增受控 Module/Service + 追加式工件 + 最小状态投影”，不需要改动三 Agent 约束、F10 Runtime、Product Approval、Plan Approval 或两阶段 Design Exploration。

