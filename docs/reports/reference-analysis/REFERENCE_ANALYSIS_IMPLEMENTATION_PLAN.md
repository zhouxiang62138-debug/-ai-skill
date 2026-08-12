# Reference Analysis Module 分阶段实施计划

## 计划范围

本计划建立在 `REFERENCE_ANALYSIS_ARCHITECTURE_AUDIT.md` 的 R0 PASS 结论上，覆盖 R1～R9。
它不是本轮生产代码实施授权；当前只完成 R0 报告，下一阶段必须由用户单独指令启动。

约束不变：Reference Analysis 是 Module / deterministic capability / analysis pipeline，
核心 Agent 仍只有 Planner、Generator、Evaluator。所有阶段遵守：

```text
Inspect → Plan → Implement → Tests → Self Audit → Report → Stop
```

每个 Phase 单独交付、单独验收；一个 Phase 失败时停止，不自动进入下一 Phase。

## 总体目标架构

```text
Reference Source
  → Source Adapter / Security Gate
  → Normalized Evidence
  → Scope Resolver
  → Domain Findings
  → Multi-Reference Synthesis
  → append-only Artifacts
  → Planner Product Proposal
  → Approved Reference Decisions
  → Generator / Evaluator scoped inputs
```

建议正式代码位于 `runtime/reference_analysis/`，不放入 `experimental/`。建议的内部边界：

- `protocol.py`：Source、Scope、Mode、Domain、Trust 和版本协议。
- `registry.py`：配置驱动 Adapter Registry；Python 不硬编码未来全部 Source Type。
- `module.py`：ReferenceAnalysisModule 的生命周期、幂等、错误、恢复和 Runtime Bridge。
- `adapters/`：第一阶段 `text_description`、`image`、`web_page`。
- `normalizer.py`：来源 hash、Evidence、confidence、observed/inferred/unknown 归一化。
- `scope.py`：用户要参考什么、明确排除什么、CR 绑定和冲突处理。
- `findings.py`：Product/UX/Layout/Visual/Technical 等通用 Finding Model。
- `synthesis.py`：Adopt/Adapt/Avoid/Unknown、来源链和优先级融合。

## 统一优先级和不可变原则

产品决策优先级：

```text
用户明确要求
>
已批准 Product Proposal / Product Spec / Plan
>
Reference Synthesis
>
单个 Reference Finding
>
模型自行推断
```

实施门禁优先级：

```text
Approved Plan > Product Spec > 已批准 Reference Decision > 原始 Reference
```

任何阶段不得：

- 把 Reference 直接当 Requirements、Acceptance Criteria 或 Approved Plan。
- 用 Reference 改写用户明确排除项、验收阈值或产品范围。
- 让 Planner 重新抓 URL、让 Generator 直接按 URL 猜设计。
- 把原文、HTML、图片、截图、仓库文件或视频帧写进 `project.yaml`。
- 修改或覆盖已有 Reference、Synthesis、Proposal、Plan、Evidence 或 CR 工件。

## Phase R1 — Protocol & Schema

### 目标

只建立可扩展协议、Schema、配置和模板；不实现 Adapter，不抓 URL，不分析图片，不写运行时
生产逻辑。

### 预期文件

```text
config/reference_analysis.yaml
config/schemas/reference_source_v1.schema.json
config/schemas/reference_scope_v1.schema.json
config/schemas/reference_analysis_v1.schema.json
config/schemas/reference_finding_v1.schema.json
config/schemas/reference_synthesis_v1.schema.json
config/schemas/reference_evidence_v1.schema.json
templates/reference_source.yaml
templates/reference_scope.yaml
templates/reference_analysis.yaml
templates/reference_finding.yaml
templates/reference_synthesis.yaml
templates/reference_evidence.yaml
```

必要时追加：

- `config/workflow.yaml` 的 `REFERENCE_ANALYSIS` 路由和迁移规则。
- project schema v6/v7 的可选 Reference 投影字段。
- `config/role_policies.yaml` 的 Reference Module 声明和字段 Ownership。
- `config/context.yaml` 的 Module/Role Reference 来源规则。
- `config/change_request.yaml` 的 CR Reference artifact path。

### 协议要求

Source 必须至少支持：

```yaml
reference_id: REF-001
source_type: web_page
reference_mode: adaptation
requested_scope: {}
explicit_exclusions: []
source: {}
provenance: {}
trust: untrusted_reference_data
```

首阶段实现集合为 `text_description`、`image`、`web_page`；协议必须允许未来加入
document、pdf、video、screen_recording、design_file、repository、source_code、
existing_project、brand_guideline、competitor_product、multiple_references。

Finding 必须区分：

- `observed`：来源直接可见或可读取。
- `inferred`：基于可追溯证据的有限推断，必须带 confidence。
- `unknown`：当前来源无法证明，不能用模型猜测填充。

数值必须支持范围和 confidence；禁止把不确定的 sidebar 宽度、响应式行为、hover、动画或
键盘交互伪装为精确事实。

### R1 验收

- 所有 Schema 可解析，版本和 ID 规则一致。
- Source Type 通过配置扩展点表达，不要求 Python 为每种类型增加分支。
- Schema 不要求大文件正文进入 YAML/project state。
- v3-v7 旧项目可继续只读校验；无 Reference 的旧项目默认行为不变。
- Schema 测试和 Workflow 配置测试通过。

### R1 停止条件

只完成协议工件和测试。不得创建 `runtime/reference_analysis/` 业务实现，不得进入 R2。

## Phase R2 — Core Reference Module

### 目标

实现纯 Module 管线和正式工件提交边界，真实支持 `text_description`、`image`、`web_page`；
未实现 video/Figma/PDF/GitHub Repository 等 Adapter。

### 主要能力

1. `ReferenceRegistry` 按配置选择 Adapter，并拒绝未注册类型。
2. `ReferenceAdapter` 输入经过 F12/F11 校验的来源，输出受控 Normalized Reference。
3. `ScopeResolver` 计算 requested scope、explicit exclusions、Reference Mode 和冲突。
4. Normalizer 生成 Evidence、hash、大小、MIME、viewport/来源元数据和 unknown。
5. Domain Analyzer 输出通用 Findings，不依赖原产品内部组件名。
6. Synthesis Engine 按来源负责的维度合并多个 Reference，生成 `REFDEC-*`。
7. `ReferenceAnalysisModule` 使用 Runtime Bridge 追加工件、提交小型状态指针和幂等事件。

### 建议工件

```text
memory/references/reference-001/
  source-001.yaml
  scope-001.yaml
  analysis-001.yaml
  findings-001/*.yaml
  evidence/manifest-001.yaml
memory/references/synthesis/reference-synthesis-001.yaml
artifacts/references/reference-001/evidence/*
```

所有新版本通过 `supersedes` 连接，目标文件存在时拒绝覆盖。大对象保存为 artifact 或
受控结果引用，Event 只保存 reference ID、artifact path、hash、大小、状态和错误码。

### Runtime 接口要求

- Module 必须使用 `ActorType.MODULE`、F10 Worker Lease、expected revision 和 CAS。
- `status`、`next_role`、`active_module`、`active_change_request` 继续由 Runtime CAS 独占。
- 需要把当前只允许 `change_request` 的 Module Commit 扩展为配置驱动的受信 Module Bridge，
  或提供等价的 `ReferenceAnalysisRuntimeBridge`；禁止复制第二套 CAS。
- 重复调用以 `project_id + reference_id + source_hash + scope_hash + analysis_version`
  形成幂等身份；恢复时先检查已有工件和 hash，再补齐缺失工件，不重写非空历史。
- 当前 F10 没有 Reference 专用 Event，可先复用 `ARTIFACT_STAGED/COMMITTED`，必要时在
  R2 追加受控 Reference 事件类型；任何新事件都必须服从 Payload 上限和 Secret 检查。

### R2 验收

- 同一输入重跑结果稳定、幂等、不会产生重复版本。
- 多 Reference 的 Synthesis 可追溯到每个 Finding 和 Evidence。
- unknown、范围估计和 confidence 保留，不伪造事实。
- 未注册 Source Type、越界路径、私网/不安全 URL、超大/不支持文件默认拒绝。
- Module 不出现在 `CORE_ROLES`、`next_role` 或 Phase Runner。

## Phase R3 — First-Ask & Planner Integration

### First-Ask

更新 `intake/first_ask.md`、需求快照模板和相关校验：

- 从原始请求中识别 URL、图片/附件、文本描述、Repository 等 Reference 线索。
- 记录 `reference_id`、source type、Mode、Scope、Exclusions、原话来源和状态。
- Scope 不明确且会影响产品方向时最多追加少量高价值问题；已回答、明确未决定或不适用项不
  得重复询问。
- Reference 与业务 Requirements 分区保存；不将外部页面文字解释为用户需求。
- 无 Reference 的项目仍沿用当前 `INTAKE → PLANNING`。

### Workflow

建议新增最小投影字段：

```yaml
reference_status: none | provided | scope_pending | ready | blocked
reference_analysis_status: not_started | running | completed | blocked
active_reference_synthesis: null | memory/references/synthesis/reference-synthesis-<nnn>.yaml
```

新项目有 Reference 时：

```text
INTAKE / first_ask_intake
  → REFERENCE_ANALYSIS / reference_analysis
  → PLANNING / planner
```

需要同时更新状态枚举、`active_module` 允许值、state transitions、state guards、role
selection 测试和 `project_state.py` 语义校验。旧项目首次读取仍只读，不自动补写 Reference
字段或迁移状态。

### Planner

更新 `prompts/planner_prompt.md`、`templates/product_proposal.md` 和 Proposal source chain：

- Planner 先读取 `active_requirements`，再读取 `active_reference_synthesis`。
- Proposal 增加 `Reference Integration`，逐项列出 Adopted、Adapted、Rejected、Unknown。
- Planner 不把 Synthesis 当 Requirements，不重新抓网页，不读取未选中原始 Reference。
- Reference 指定视觉方向时，Design Exploration 生成 close/adapted/originalized 三条路线，
  仍执行用户确认、产品批准和 Plan 批准。

### R3 验收

- 无 Reference 场景旧流程回归通过。
- 单 Reference、多 Reference、明确 Exclusions、Scope 修改和 Scope 不明确场景通过。
- Planner source chain 能发现缺失、越界、hash 不匹配或未完成 Synthesis。
- First-Ask 不重复已回答的 Reference Scope 问题。

## Phase R4 — Reference-Guided Design Exploration

### 目标

将 Reference 作为设计探索输入约束，而不是替代设计确认。

### 实现要点

- 只在 Synthesis 的 Visual/Layout/UX Decision 进入 Proposal 后触发 Reference-guided 探索。
- 每轮仍生成恰好 3 个完整路线：
  1. `close`：尽量接近结构和视觉语言，但不默认复制 Logo、商标、品牌资产、专有图像和原文案。
  2. `adapted`：保留模式和层级，结合当前产品重新设计。
  3. `originalized`：只保留核心原则，形成更明显的原创方案。
- 每个路线继续使用现有 `concept.md`、`preview.html`、`preview.css` 和差异校验。
- 设计反馈、选择、融合、否定和历史恢复继续追加；设计选择不等于 Product Approval。

### R4 验收

- Reference-guided 三路线在定位、用户路径、信息架构或特色功能上真实不同。
- 没有 Reference 的 Design Exploration 行为不回归。
- 预览中能追踪 Reference Decision，但不把原网页代码/品牌资产复制进生产代码。

## Phase R5 — Generator Integration

### 目标

让 Generator 只执行已经进入批准来源链的 Reference Decision。

### 实现要点

- 扩展 Generator Preflight：验证 `approved_plan`、Product Spec、产品批准、Plan 批准以及
  Reference Decision 的引用和 hash。
- 只有 Plan/Spec 中明确采用的 `REFDEC-*` 才能进入 Generator Context；原始 URL、未选 Synthesis、
  未批准 Finding 不得成为实现输入。
- 明确执行顺序：Approved Plan > Product Spec > Approved Reference Decision。
- Generator 不能修改评分规则、Reference Schema、Reference 原始历史或验收阈值。

### R5 验收

- 直接传入 URL 或未批准 Reference Decision 的 Generator Preflight 失败。
- Approved Plan 与 Synthesis 冲突时按 Approved Plan 实施并留下可追踪结果。
- 无 Reference 的 Generator Preflight 和原有 source chain 完全回归。

## Phase R6 — Evaluator & Evidence

### 目标

只对已经进入 Acceptance Criteria 的 Reference Decision 验收；建立正式 Reference Conformance
Evidence，不以主观“像不像”判 FAIL。

### 实现要点

- 扩展 Evaluator Context：只读 AC 关联 `REFDEC-*`、Reference Evidence Manifest 和实现证据。
- 如 Evaluation Profile/Approved AC 明确要求，启用可选 `REFERENCE_CONFORMANCE` Gate；默认不启用。
- Visual/Layout 对比使用结构化维度：Layout Structure、Visual Hierarchy、Spacing、Component
  Geometry、Typography Relationship、Color Relationship、Responsive Behavior。
- 复用 Browser Harness 的底层截图/DOM/CSS/Design Token 证据和现有 Evidence Manifest、Verifier、
  Attestation；新增 Reference Evidence 字段时只存 hash、viewport、来源和相对引用。
- 不能从静态图片证明的交互、动画或响应式行为标记 unknown，不作为失败依据。

### R6 验收

- 没有正式 AC 的 Reference 差异不能导致 FAIL。
- 具备 AC、Decision、Evidence 三向来源链时才能进行 Reference Conformance。
- Browser unavailable、证据缺失、hash 不匹配和 Evidence 篡改按现有 Gate/Blocked 规则处理。
- 原有 Browser Acceptance、Feature Completeness、Regression Gate 不回归。

## Phase R7 — Change Request Integration

### 目标

支持 `ACCEPTED/ARCHIVED → Change Request + New Reference`，不污染原项目历史。

### 实现要点

- `scripts/change_request.py` 创建 CR 后，可调用 ReferenceAnalysisModule，输入带 `CR ID`、
  项目 revision 和 stable baseline。
- CR 工件布局：

```text
change_requests/CR-0001/
├── request.yaml
├── references/
│   ├── reference-001/...
│   └── synthesis/reference-synthesis-001.yaml
├── impact-analysis-001.yaml
├── approvals/
├── baseline/
├── handoffs/
└── evaluations/
```

- Reference Analysis 在 Planner 影响分析前串行完成；CR 的 Scope/Decision 只能写入 CR 专属
  Plan 和获批 Change Items。
- 不更新原项目批准 Proposal/Plan，不覆盖全局 Reference Synthesis，不允许跨项目引用。
- Evaluator 必须对 CR 新要求和原功能执行回归，Release 成功后才回到 `ACCEPTED`。

### R7 验收

- 一个项目同一时刻只允许一个 active CR。
- CR Reference 无法通过 `change_request_id`、project ID、baseline revision 和 hash 校验时阻断。
- 取消 CR 不删除 Reference 历史；原项目状态和原有 Reference 不受污染。

## Phase R8 — Security Hardening

### 必测安全场景

- 恶意网页正文包含 Prompt Injection。
- URL 重定向到不同 host、私网、localhost、metadata、非 HTTPS 或带凭据地址。
- DNS/响应大小/下载超时、压缩炸弹、错误 MIME、恶意图片/文档和不支持媒体。
- 本地绝对路径、`..`、symlink、junction、跨项目路径逃逸。
- 恶意 Repository、README/注释中的指令和代码执行诱导。
- Event、Context Manifest、日志、project.yaml 和 artifacts 泄露 Secret。
- Reference Artifact 篡改、hash 不匹配、重复幂等调用和中途崩溃恢复。
- Module 试图写 `code/`、`memory/requirements/`、受保护 Proposal/Plan 或 Evaluator 规则。
- Network/Browser/External Tool capability 未授权时默认 DENY。

### R8 验收

Reference 内容始终作为数据处理；任何外部文本不能改变 Agent、Runtime、CAS 或 Security
Policy 指令。安全拒绝必须可审计但不记录 Secret、Authorization、敏感 query 或大原文。

## Phase R9 — End-to-End Tests

必须在仓库外创建、使用 `test_` 前缀的 managed test project；测试结束进入 `archive/`，保留
`TEST_REPORT.md`，不得把测试项目、截图、需求、报告或构建物写回 Skill 目录。

至少覆盖：

1. 记账 App，无 Reference：旧流程完全不变。
2. 截图 + 布局/视觉 Scope：生成 Source、Finding、Synthesis 并交给 Planner。
3. 网站 A 视觉 + 网站 B 导航：多 Reference 维度融合。
4. 明确“不参考品牌”：Logo/品牌/文案进入 Avoid 且不进入实现决策。
5. 用户修改 Reference Scope：追加新 Scope/Analysis/Synthesis，不覆盖旧版本。
6. Reference 与 Requirements 冲突：Requirements 获胜并留下冲突记录。
7. Reference-guided Exploration → 用户选择 → Product Approval → Plan Approval → Generator → Evaluator。
8. ACCEPTED → Change Request + 新 Reference → 批准修改 → 回归 → Release → ACCEPTED。
9. 恶意 Reference Prompt Injection：不能改变 Agent 或 Runtime 指令。

### R9 必须验证的 Runtime 不回归

- F10 Session/Event/Lease/CAS/Revision/Recovery/Idempotency。
- F11 Execution Broker、Path Policy、Snapshot/Restore 和项目边界。
- F12 Capability、Network、External Tool、Credential/Secret Boundary。
- F13 Context Budget、Role Scope、Manifest、Resume/Delta 和 rollover。
- 现有 Browser Acceptance、Evaluation、Change Request 和 Plan Approval 流程。

## 兼容、迁移和恢复策略

### 旧项目

- v3-v6 首次读取只读，不自动写 Reference 字段、不自动改状态、不自动建立 Synthesis。
- 无 Reference 的旧项目继续按现有状态机运行。
- 只有明确的迁移/检查/预览流程才升级到包含 Reference 状态的新版本；迁移必须追加记录、
  可验证、可回滚。
- v7 `project.yaml` 写入始终通过 Worker Lease + expected revision + CAS。

### 追加式和幂等

- 任何 source/scope/analysis/finding/synthesis/evidence 版本目标存在时拒绝覆盖。
- 每个版本都记录 `supersedes`、source hash、Scope hash、生成器/Adapter 版本、项目 ID、
  可选 CR ID 和 Evidence refs。
- 恢复只补齐缺失工件，不重做已经提交且 hash 一致的工件；hash 不一致时阻断，不静默覆盖。
- 事件和 artifact 提交顺序应是：验证输入 → Stage → 写入 hash/Manifest → Commit artifact →
  CAS 更新小型 project pointer → 记录完成事件；任何失败保留可诊断状态。

## 分阶段交付报告要求

每个阶段报告必须包含：

```text
Phase:
Status:
Files changed:
Architecture decisions:
Tests:
Regression:
Known limitations:
Next recommended phase:
```

Phase 报告不得把未实现的 Adapter、Browser Reference、视觉比较、Security Hardening 或
Change Request 支持宣称为已完成。只有可复现测试证据和来源链完整时，才能写 PASS。

## 当前阶段结论

```text
Phase: R0 — Current Architecture Audit
Status: PASS
Files changed: 仅本审计目录下的两份 R0 报告
Architecture decisions: 见 REFERENCE_ANALYSIS_ARCHITECTURE_AUDIT.md
Tests: R0 为只读审计；未修改生产代码，下一阶段按 R1 增加 Schema/Workflow 测试
Regression: 本阶段不触碰现有生产代码和工作流
Known limitations: Source Adapter、Schema、Module Bridge、Context Scope 尚未实现
Next recommended phase: R1 / DO NOT START R2
```

R0 完成后停止，等待用户明确授权 R1。
