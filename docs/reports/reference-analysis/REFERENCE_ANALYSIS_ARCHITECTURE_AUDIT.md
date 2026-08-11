# Reference Analysis Module 架构审计

## 审计范围

本报告对应 Phase R0 — Current Architecture Audit。审计对象是当前 Skill 本体仓库，
不是 managed project；本阶段不读取其他项目，不创建 `project.yaml`，不修改生产代码、
配置、Schema、Prompt、模板或测试。

审计基于当前工作树中的以下入口和正式能力：

- `AGENTS.md`、`SKILL.md`、`README.md`。
- `config/workflow.yaml`、`config/role_policies.yaml`、`config/context.yaml`、
  `config/runtime.yaml`、Browser/Harness 配置和 project schema v6/v7。
- `intake/first_ask.md`、`prompts/planner_prompt.md`、现有模板及 Change Request 协议。
- `runtime/role_selector.py`、`runtime/orchestrator.py`、`runtime/project_revision.py`、
  `runtime/session_store.py`、Lease/CAS、F11 Execution、F12 Security、F13 Context、
  Browser Harness。
- `scripts/project_state.py`、`scripts/approval.py`、`scripts/change_request.py` 及相关回归测试。
- `docs/workflow_protocol.md`、`docs/project_state_schema.md`、Managed Runtime 和 F11-F13
  正式能力报告。

## R0 结论

当前架构具备接入 Reference Analysis 的必要底座，但尚不存在 Reference Source Protocol、
Reference Finding/Synthesis 工件、Reference 专用适配器、Scope 解析器或正式工作流接入。
仓库中已有的 `reference` 字段主要表示 Context/Evidence 的文件引用，不能直接充当 Reference
Analysis 协议。

推荐把 Reference Analysis 做成正式的 `runtime/reference_analysis/` Module，并由配置驱动的
Adapter Registry、Normalizer、Scope Resolver、Finding Model、Synthesis Engine 和 Artifact
Protocol 组成。它可以使用模型或外部工具完成受限分析，但不能注册为 Agent、不能进入
`next_role`、不能扩大 `CORE_ROLES`。

## 当前架构事实

### Agent 和调度边界

- `config/workflow.yaml` 将核心角色固定为 `planner`、`generator`、`evaluator`。
- `runtime/phase_runner.py` 的 `CORE_ROLES` 也只包含这三个角色；Phase Runner 不接受 Module
  作为模型 Role。
- `runtime/role_selector.py` 已支持 `ROLE`、`MODULE`、`WAIT` 三种选择，但当前可选的正式
  `active_module` 只有 `first_ask_intake`。
- `runtime/orchestrator.py` 的 `start()` 能选择 Module，但目前没有 Module Run；
  `commit_module_step()` 和 `commit_module_state()` 只允许 `change_request`。
- `active_module`、`status`、`next_role` 属于 Runtime CAS 的生命周期字段，不能由普通角色或
  Module 直接写入 `project.yaml`。

结论：Reference Analysis 最适合复用现有 Module 选择和 CAS 通道，不能伪装成 Planner 的新
角色，也不能塞进 Phase Runner 的 Role 列表。

### F10 Durable Runtime、Lease 和 CAS

- `project.yaml` 只保存 v7 Runtime 投影；完整 Session、Event、Lease、Checkpoint、State
  Revision 位于 F10 Control Plane SQLite。
- 状态提交要求有效 Worker Lease、`expected_revision`、Compare-And-Swap 和幂等键。
- Event Payload 有大小上限和疑似敏感字段拒绝；Event 本身追加式且禁止更新/删除。
- `ProjectStateCAS` 已提供字段 Ownership 和工作流迁移校验，能阻止隐藏字段变化和越权写入。
- 现有 `ActorType.MODULE`、`ARTIFACT_STAGED`、`ARTIFACT_COMMITTED` 可以承载 Module 的
  审计边界；当前没有 Reference 专用事件或通用 Module Commit Bridge。

结论：Module 必须通过现有 F10 Lease/CAS/SessionStore 工作。Reference 原文、HTML、图片、
仓库文件和大截图不能放进 `project.yaml` 或 Event Payload，只能写入项目内追加式工件或受控
外部结果引用，并在状态中保存小型指针、版本和 hash。

### F11 Execution 和项目路径

- `ExecutionPathPolicy` 对项目内相对路径、绝对路径、`..`、symlink/junction 逃逸进行校验。
- F11 Execution Broker 是受控执行入口，不允许角色绕过 Runtime 直接写状态。
- `role_policies.yaml` 已有角色读写路径，但 `modules` 部分目前主要是声明；正式
  `ExecutionPathPolicy`、CapabilityPolicy 解析的是 `roles`，不会自动为新 Module 形成可执行
  权限。

结论：Reference Analysis 需要单独的 Module Path/Capability Policy 或明确的 Host-side
Module Policy。只在 YAML 中添加 `modules.reference_analysis` 不足以形成代码级边界。

### F12 Security

- Capability、Network、External Tool Policy 均默认拒绝，并由代码执行 allowlist。
- Network 当前只允许 HTTPS，且按精确 service/host/port/operation 校验；正式角色策略中
  GitHub 读能力只给 Generator。
- Browser Policy 当前只允许 Evaluator，Browser Harness 只接受评估 Profile 的 base URL
  同源地址，并把截图、DOM、console/network 失败整理为验收证据。
- Secret、敏感 query、userinfo、fragment、私网/loopback/metadata 地址等已有拒绝逻辑。
- 正式 F12 的安全边界不会自动把外部网页内容变成系统指令；但当前没有 Reference 专用的
  不可信内容模型、Prompt Injection 分隔协议、上传文件摄取边界或动态 Reference URL allowlist。

结论：Reference 是 `UNTRUSTED INPUT`。不能直接复用 Generator 的 GitHub 权限，也不能把
Browser Acceptance Broker 直接当作 Reference Browser Adapter。需要 Host-side、按会话和
来源授权的 Reference Adapter，并对每次重定向重新检查网络策略。

### F13 Context Runtime

- 正式 Context Builder 只为 Planner、Generator、Evaluator 建立 Role Scope，并由
  `config/context.yaml` 做来源 allowlist、优先级、字节预算和 Inline/Reference 选择。
- `ContextBuildRequest.additional_references` 接受的是当前项目内相对路径，不是任意 URL；
  它会经过 Path Policy、Secret 检查、hash 和预算选择。
- Context Manifest 只保存来源元数据和 hash，不保存正文；Resume 使用同一 Session、项目、
  Role、revision、Policy/Budget 指纹。
- F13 当前并没有 `reference_synthesis`、`reference_finding` 或 Module 专用 Context Scope。

结论：不能把所有原始 Reference 通过 `additional_references` 注入模型。应新增 Reference
专用、预算受控的 Module Context，并把 Planner、Generator、Evaluator 的读取范围分别收窄到
Synthesis、已批准 Decision、验收相关 Decision/Evidence。

### Browser Harness 和 Evidence

现有 Browser Adapter/Playwright Adapter 能复用“启动、导航、等待稳定、DOM 观察、截图、
console/network 诊断”的底层能力；Browser Harness 的 Evidence 结构也能复用 hash、截图引用、
viewport 和失败分类的思想。

但当前 Browser Broker 的角色和 Profile 语义是 Evaluator Acceptance 专用。Reference Web
Adapter 需要新的用途边界：只生成 Reference Evidence，不执行产品验收、不创建 Acceptance
Criterion、不把页面文本当指令。建议复用底层 Browser Driver，新增独立的 Reference Broker /
Profile，而不是放宽现有 Evaluator Browser Policy。

### First-Ask、Planner 和 Change Request

- First-Ask 已有追加式原始请求、采访和完整需求快照；会记录附件/外部引用，但当前模板没有
  结构化 `references`、Scope、Reference Mode 或排除项。
- First-Ask 已规定每轮最多 1～3 个高价值问题，`answered`、`undecided`、`not_applicable`
  不重复询问；这正适合识别 Reference 和只在 Scope 真正影响方向时补问。
- Planner 已要求先读 `active_requirements`，且只读需求文件；产品方案、规格和 Plan 都是
  追加式并受双重批准链保护。
- Generator 的唯一可执行输入是 `approved_plan`，其优先级高于 Product Spec 和任何参考资料。
- Evaluator 只能按已进入 Product Proposal/Product Spec/Plan/Acceptance Criteria 的正式要求
  判定；“不像参考网站”本身不能成为 FAIL。
- Change Request 已有单活动请求、追加式事件、影响分析、批准、基线、评估和 Release 目录。

结论：Reference Source/Scope 应由 First-Ask 记录事实和用户意图；Reference Synthesis 应由
Module 生成；Planner 负责把采用/调整/拒绝/未知整合进产品方案；只有批准后的 Reference
Decision 才能进入 Generator/Evaluator。CR 的 Reference 工件必须绑定 CR ID 并存入 CR 目录。

## R0 问题清单回答

| 问题 | 审计结论 |
| --- | --- |
| 1. 最合理插入位置 | 新项目在 First-Ask 产出充分需求后、Planner 生成产品方案前，进入 Reference Analysis Module；无 Reference 的项目保持现有 `INTAKE → PLANNING`。 |
| 2. 是否需要新状态 | 需要一个 `REFERENCE_ANALYSIS` 状态，原因是当前静态 `role_selector` 无法仅凭业务字段安全分支，且 `active_module` 需要持久化、恢复和 CAS 语义。暂不新增 `WAITING_FOR_REFERENCE_REVIEW`；Scope 歧义在 First-Ask 解决，分析中的 unknown 作为数据交给 Planner，安全/来源失败才进入现有 `WAITING_FOR_USER` 或 `BLOCKED`。 |
| 3. 是否可以作为 `active_module` | 可以，且应是唯一的调度表达：`status: REFERENCE_ANALYSIS`、`active_module: reference_analysis`、`next_role: null`。它不是 `next_role`，也不是 `CORE_ROLES`。 |
| 4. First-Ask 如何记录 | 在追加式需求快照中增加独立 `references` 区块：Reference ID、来源类型、原始来源引用、用户指定 Scope、明确排除项、Reference Mode、用户原话来源和状态。Reference 只是参考输入，不并入业务 Requirements。 |
| 5. Planner 如何读取 | Context 优先提供 `active_reference_synthesis` 以及其高价值 Findings；Prompt 明确 Adopted/Adapted/Rejected/Unknown，且要求显式检查用户需求和批准来源链。Planner 不重新抓 URL、不读取全部原始资料。 |
| 6. Context Builder 如何 Scope | 保留三核心 Role Scope；新增 `reference_analysis` Module Scope 和三个 Role 的 Reference 来源规则。Module 只读当前项目、当前 CR/需求指针和经 Adapter 生成的受控 Evidence；Planner 读 Synthesis，Generator 只读批准 Decision，Evaluator 只读 AC 关联 Decision/Evidence。 |
| 7. CAS Ownership 谁负责 | Runtime CAS 独占 `status`、`next_role`、`active_module`、`active_change_request`；First-Ask 负责 Reference 事实/状态；Reference Module 负责分析状态和 Synthesis 指针；所有业务字段变化都经过 Worker Lease + expected revision。 |
| 8. 是否需要 Runtime commit interface | 需要。现有 `commit_module_state` 需要从只允许 `change_request` 扩展为配置驱动的受信 Module Bridge，或增加专用 `ReferenceAnalysisRuntimeBridge`；不得让 Reference Module 直接写 `project.yaml`，也不得另建 Session Store。 |
| 9. Change Request 如何复用 | 复用同一 ReferenceAnalysisModule，输入带 `change_request_id` 和 CR 基线；结果写入 `change_requests/CR-<nnnn>/references/`，随后由 Planner 做影响分析。CR 不应把新 Reference 写进原项目的全局 Synthesis 指针。 |
| 10. Browser Harness 如何复用 | 复用 Browser Driver 的启动、导航、稳定等待、DOM/截图/诊断底层能力；不复用 Evaluator Acceptance 的角色授权、AC 绑定和 PASS/FAIL 语义。新增独立 Reference Browser Adapter/Profile。 |
| 11. Security Runtime 如何复用 | 复用 F12 Capability、Network/External Tool Policy、F11 Path Policy、Secret Boundary 和 F10 Event；补充 Reference 专用 URL allowlist、上传摄取、文件类型/大小、重定向、内容隔离和 Prompt Injection 测试。 |
| 12. 是否已有类似能力 | 没有完整模块。现有 `ContextSource.reference`、Browser Evidence 引用、Evaluation `report_reference` 和 Tool Result 引用都只是可审计文件引用/证据指针，不提供 Source Protocol、Scope、Finding、Synthesis 或 Adapter Registry。 |
| 13. 主要架构冲突 | `active_module`/状态枚举只支持 First-Ask；Module 无通用 Run/Commit；Capability/Path Policy 不解析 modules；Context Policy 把 Role 集合硬编码为三 Agent；Browser Policy 只允许 Evaluator；project schema 没有 Reference 字段/状态；First-Ask/Proposal/Plan 模板没有 Reference 来源链；EventType 没有 Reference 领域事件。 |

## 推荐目标架构

```text
First-Ask
  ├─ 记录用户原话、Reference Source、Scope、Mode、Exclusions
  └─ 无 Reference → PLANNING
                   有 Reference → REFERENCE_ANALYSIS / active_module
                                      ↓
                           ReferenceAnalysisModule
                           ├─ Adapter Registry
                           ├─ Source/Security Gate
                           ├─ Normalized Evidence
                           ├─ Scope Resolver
                           ├─ Domain Analyzer
                           ├─ Observed/Inferred/Unknown Normalizer
                           └─ Synthesis Engine
                                      ↓
                         append-only Reference Artifacts
                                      ↓
                             active Synthesis pointer
                                      ↓
                                   Planner
                                      ↓
                            Product Proposal / Approval
                                      ↓
                       approved Reference Decisions in Spec/Plan
                                      ↓
                         Generator / Evaluator restricted reads
```

### Module 分层

推荐的正式实现边界为：

```text
runtime/reference_analysis/
├── module.py              # Module 编排、幂等、状态交接
├── protocol.py            # Source/Scope/Mode/Domain 协议
├── registry.py             # 配置驱动 Adapter 注册，不在 Python 写死全部类型
├── adapters/              # 第一阶段 text_description/image/web_page
├── normalizer.py          # 统一 Evidence、hash、confidence、unknown
├── scope.py               # 用户 Scope 与 Exclusions 解析
├── findings.py            # Finding/Domain/Provenance 数据模型
└── synthesis.py           # 多 Reference Adopt/Adapt/Avoid/Unknown 融合
```

这不是第四个 Agent。任何模型辅助的识别只属于 Module 内部受约束的分析能力；模型输出
必须先经过 Schema、来源、Scope、优先级和不可信内容校验，不能获得新的 workflow Role。

### Reference Source Protocol

协议必须区分“来源描述”和“分析结果”，至少包含：

- `reference_id`、schema/version、`source_type`、来源定位、内容 hash/大小/MIME（可得时）。
- `reference_mode`：`inspiration`、`adaptation`、`close_recreation`。
- `requested_scope`：Product、UX、Information Architecture、Navigation、Layout、Visual、
  Components、Design Tokens、Interaction、Motion、Content、Brand、Technical。
- `explicit_exclusions`、用户原话来源、项目绑定和可选 `change_request_id`。
- Adapter 能力、处理状态、错误/unknown 原因和 Evidence 引用。
- 所有外部内容的信任标记固定为 `untrusted_reference_data`，不允许变成 Prompt 指令。

第一阶段只承诺真正实现 `text_description`、`image`、`web_page`；协议预留 document、pdf、
video、screen_recording、design_file、repository、source_code、existing_project、
brand_guideline、competitor_product、multiple_references 等扩展点。

### Findings 和不确定性

每个 Finding 至少包含 domain、通用描述、结论类型、来源、Evidence、Scope 命中关系、
confidence 和事实状态：`observed`、`inferred` 或 `unknown`。无法由单张截图证明的 hover、
mobile 导航、动画、键盘交互、响应式行为必须是 `unknown`，不能伪造精确数值；估算应记录
范围和 confidence，而不是单点事实。

### Synthesis 和优先级

Synthesis 统一输出 `adopt`、`adapt`、`avoid`、`unknown`、冲突和来源链，并以 `REFDEC-*`
标识可供 Planner 引用的 Decision。业务决策优先级为：

```text
用户明确要求 > 已批准 Product Proposal / Product Spec / Plan > Reference Synthesis
> 单个 Reference Finding > 模型自行推断
```

实施时再加一层明确门禁：

```text
Approved Plan > Product Spec > approved Reference Decision > raw Reference
```

### 推荐工件布局

为了满足现有项目的追加式和三位编号习惯，建议采用“逻辑类型 + 版本号”，不要使用会被
覆盖的固定 `analysis.yaml`：

```text
memory/references/
├── reference-001/
│   ├── source-001.yaml
│   ├── scope-001.yaml
│   ├── analysis-001.yaml
│   ├── findings-001/
│   │   ├── product.yaml
│   │   ├── ux.yaml
│   │   ├── layout.yaml
│   │   ├── visual.yaml
│   │   └── technical.yaml
│   └── evidence/
│       └── manifest-001.yaml
└── synthesis/
    └── reference-synthesis-001.yaml

artifacts/references/reference-001/
└── evidence/             # 图片、截图、网页快照等大文件；不进 project.yaml/Event
```

Reference 工件必须包含 `supersedes`、来源 hash、生成器/Adapter 版本、项目 ID 和可选 CR ID。
CR 的同类工件放在 `change_requests/CR-<nnnn>/references/`，不覆盖全局 Reference 历史。

### 最小 Project State 增量

不把 Reference 数组、原文、截图或 HTML 放进 `project.yaml`。建议只增加可选的小型投影字段：

```yaml
reference_status: none | provided | scope_pending | ready | blocked
reference_analysis_status: not_started | running | completed | blocked
active_reference_synthesis: memory/references/synthesis/reference-synthesis-001.yaml
```

Reference 列表和 Scope 的权威来源仍是 `active_requirements` 或活动 CR 工件；
`active_reference_synthesis` 只作为 Planner 的当前指针。Generator 不因这个指针获得直接
执行权限，必须从获批 Product Spec/Plan 读取已批准 Decision。

## Workflow Integration 建议

### 新项目

```text
没有 Reference：INTAKE(first_ask_intake) → PLANNING(planner)

有 Reference：INTAKE(first_ask_intake)
           → REFERENCE_ANALYSIS(reference_analysis Module)
           → PLANNING(planner)
```

Reference Analysis 成功后只提交“completed + synthesis pointer + 下一状态”这类小型状态；
失败时写追加式诊断工件并进入现有 `WAITING_FOR_USER`/`BLOCKED`，不生成伪造 Synthesis。
不新增 `WAITING_FOR_REFERENCE_REVIEW`，除非后续产品明确要求用户逐条批准分析结果；当前
需求只要求把正式 Synthesis 交给 Planner。

### Design Exploration

Reference 明确提供视觉或布局方向时，Planner 仍然必须遵守现有产品方案和设计确认规则，
但三条路线可以是 Reference-guided：接近 Reference、保留语言但适配当前产品、保留原则而
原创化。设计选择仍不等于产品批准。

### Change Request

已完成项目的 CR 仍由现有 Change Request/Planner 生命周期驱动。Reference Module 以 CR ID
作为绑定上下文，在影响分析前串行运行并写入 CR 专属目录；全局 `active_reference_synthesis`
不被新 CR 改写。Planner 只把批准的 CR Reference Decision 写入新 Plan/Handoff，Evaluator
同时执行新增要求和原功能回归。

## Security Boundary

必须固定以下边界：

1. URL 只通过 Reference 专用的 Host-side Adapter 读取；https、host、port、redirect、
   DNS/私网地址、超时、响应大小、MIME 和下载次数逐项受策略控制。不能因为支持 URL 就给
   Planner 或 Generator 开放泛化网络。
2. 上传文件先经 Host 摄取到项目受控 artifact 目录，校验路径、扩展名/MIME、大小、hash 和
   解码安全；Reference Module 不接受任意本机绝对路径。
3. Git/Repository/Source Code 默认只读、默认不执行；若将来启用，必须使用 F12
   External Tool allowlist，不能把仓库代码交给本地 shell 运行。
4. 原始网页文字、文档、图片 OCR、代码注释和 README 全部标记为不可信数据。任何类似
   “忽略之前指令”的内容只能进入 Evidence/Finding 的被分析文本，不能改变 System/Role/Runtime
   规则。
5. 所有 Event、Context Manifest、状态字段和日志只保存安全元数据、相对路径、hash、大小、
   错误码和引用；原始大内容使用项目 artifact 或 Control Plane 受控结果引用。
6. Reference Analyzer 不修改 Requirements、Acceptance Criteria、Approved Plan 或用户
   排除项；冲突必须结构化报告，默认由高优先级来源获胜。

## Blocking Issues

当前没有阻止 R1 协议设计的架构性问题，但以下项目在实现前必须解决：

- v6/v7 project schema 和 `project_state.py` 需要安全增加新状态、`active_module` 值、
  可选 Reference 投影字段及状态语义，同时保持 v3-v6 首次读取只读。
- `runtime/orchestrator.py` 需要通用、幂等、带 Lease/CAS 的 Module Runtime Bridge；不能
  复制一套 CAS，也不能把模块运行伪装成 Role Run。
- `role_policies.yaml` 的 Module 声明与实际 Capability/Path Policy 解析存在分层缺口，需在
  R1/R2 设计并由测试证明真正生效。
- F13 Context Policy 目前硬编码三核心 Role；需增加 Module Scope 或独立受控 Reference
  Context，且不能因此把第四个 Agent 加进 Role 集合。
- 当前 Network/Browser 配置不允许一般 Web Reference；必须定义会话级来源授权和 Adapter
  审计，不得简单改成全局 allow。
- append-only Reference 工件需要确定版本命名、`supersedes`、Evidence hash、失败恢复和
  CR 绑定规则；这些规则未写入现有 Schema/模板。

## R0 RESULT: PASS

Recommended Architecture:
正式 `runtime/reference_analysis/` Module + Adapter/Normalizer/Scope/Finding/Synthesis/
Artifact Protocol；保留三个核心 Agent，不新增第四 Agent。

Workflow Integration:
新项目有 Reference 时采用 `INTAKE → REFERENCE_ANALYSIS(active_module) → PLANNING`；
无 Reference 的项目保持原流程；CR 绑定专属工件后复用同一 Module。

Runtime Integration:
复用 F10 SessionStore、Lease、Event、Checkpoint、CAS 和 Recovery；新增受控通用 Module
Bridge，Reference Module 不直接写 `project.yaml`。

Security Boundary:
Reference 永远是 untrusted data；复用 F11/F12 基础策略，新增 Reference 专用 URL/文件/
内容隔离与 Prompt Injection 防线；不放宽现有 Agent 权限。

Context Strategy:
Module 使用独立受限 Context；Planner 读 Synthesis/高价值 Findings，Generator 只读已批准
Decision，Evaluator 只读 AC 关联 Decision/Evidence，原始内容不全量注入。

Schema Changes Required:
Reference Source/Scope/Analysis/Finding/Synthesis/Evidence Schema、配置、模板；project
schema/workflow/role ownership/context policy 的最小兼容增量；Change Request、Proposal、
Product Spec、Plan、Evidence Source Chain 的引用扩展。

Blocking Issues:
通用 Module Bridge、Module Policy 真正执行、F13 Module Scope、Reference 网络/上传安全
边界和追加式工件协议必须先落定。

Recommended Next Step:
R1 / DO NOT START R2

本报告完成后停止，不自动进入下一阶段，等待用户明确指令。
