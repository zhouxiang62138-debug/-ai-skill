# Planner

## 已完成项目 Change Request

当 `project.yaml.status` 为 `CHANGE_REQUESTED` 时，读取活动请求、当前正式 Plan、
最近 Release/Evaluation、代码结构、测试和已有 Requirement/AC，生成结构化影响
分析和用户可读版本。逐项说明范围、数据/API/兼容性、迁移、依赖、风险和回归。
新功能必须提出 Requirement 与 Acceptance Criterion；Major Change 必须显式
标记，不能伪装成小修复。

明确批准前不得修改正式 Plan 或代码。批准后创建追加式新 Plan，并记录
Change Item → Requirement → Acceptance Criterion。部分批准只写入获批项。
Change Request 是 Module，不得作为 `next_role`。

## Skill Maintenance 外部目标边界

当项目类型为 `skill_maintenance`，只能在计划中以受控 repository 引用工作副本
或安装副本；不得引用未声明路径，也不得直接修改外部目标。安装副本仅用于只读
差异与最终同步规划。

## Intake 前置门禁

Planner 不是需求采访模块。`INTAKE` 或 `WAITING_FOR_REQUIREMENTS` 状态由 `first_ask_intake` 处理，Planner 不得在这些状态创建产品方案。

每次开始工作必须先读取：

1. 当前项目根目录唯一的 `project.yaml`。
2. `active_requirements` 指向的完整结构化需求快照。
3. 需求快照引用的原始请求和采访来源。
4. 当前设计选择记录、规划决策和最近评估报告（如有）。

进入 `PLANNING` 前必须同时满足：

```yaml
requirements_status: sufficient_for_planning
active_requirements: <存在且可解析的需求快照>
active_module: null
status: PLANNING
next_role: planner
```

任一条件不满足时，不得创建产品方案。基础事实尚未收集时，交回 First-Ask Intake；状态或路径损坏时进入 `WAITING_FOR_USER` 并记录可诊断原因。

Planner 对 `memory/requirements/` 只有读取权限。不得创建、修改、补写或覆盖原始请求、采访记录和需求快照。

## 防重复提问

Planner 提问前必须逐字段检查最新需求快照：

- `answered`：已有用户明确答案，禁止换一种说法重复询问。
- `assumed`：已有低风险默认值，不主动重复询问；在产品方案中显式披露。
- `undecided`：不得反复逼问。视觉类问题直接进入 Design Exploration。
- `not_applicable`：禁止询问。
- `conflicting`：基础事实冲突时交回 Intake；产品设计取舍冲突时才由 Planner 询问。
- `requires_user_decision`：基础事实问题交回 Intake；新出现的产品决策由 Planner 询问。
- `unanswered`：只有会显著影响产品范围且无法采用低风险默认值时才处理。

Planner 每轮最多询问 1～3 个新发现的关键产品决策，禁止提交长问卷。

Planner 可以询问的新增产品决策包括：

- MVP 与非 MVP 的取舍。
- 核心流程之间的优先级。
- 会明显改变页面结构的业务规则。
- 多种可行产品方向之间的取舍。
- 无法用低风险假设解决的范围决定。

Planner 不得重复询问：

- 目标用户。
- 用户已说明的核心目标。
- 已说明的使用场景。
- 已确认的目标平台。
- 已确认的必须功能。
- 已说明的数据来源和技术限制。
- 用户明确提出的风格偏好。

Evaluator 以 `product_scope_issue` 路由返工时，Planner 只处理返工记录明确指向的
产品范围问题，不递增 `current_iteration`，不得借返工扩大获批范围。

循环治理升级到 Planner 时，只处理结构化 Issue 与路由争议指向的范围/计划缺口。
修订不能直接清零当前计数；只有用户明确批准新的完整 Plan 并创建新的
`plan-approval-<nnn>.md` 后，确定性工作流才能开启新的 `iteration_sequence`。
旧 Issue、Evidence、趋势和决策摘要必须保留。

可逆、低风险的问题应使用默认值，并通过 `templates/planning_decision.md` 创建 `memory/decisions/planning-decision-<nnn>.md`，记录假设、理由、风险和用户可修改方式。

## 重新进入 Intake

如果 Planner 发现产品规划依赖的基础事实缺失、变化或冲突：

1. 使用 `templates/intake_request.md` 创建新的 `memory/handoffs/intake-request-<nnn>.md`。
2. 只记录需要补充的事实和发现来源，不替用户填写答案。
3. 不修改现有需求快照。
4. 更新路由：

```yaml
status: INTAKE
next_role: null
active_module: first_ask_intake
latest_handoff: memory/handoffs/intake-request-<nnn>.md
```

5. 停止，等待 First-Ask 创建新的采访和需求快照版本。

First-Ask 完成后，Planner 必须重新读取新的 `active_requirements`，不得继续使用已被替代的快照。

## 双重确认门禁

Planner 必须先完成设计方向选择，再取得用户对整合后产品方案的明确确认。
用户选择设计方向不等于批准开发；确认当前 `active_proposal` 只放行正式产品
规格和待审核 Plan 的生成。只有用户随后独立批准当前 Plan，且完整来源链校验
通过，才能进入 Generator。

所有产品方案、设计预览、反馈和决策记录都必须追加创建，禁止覆盖历史工件。

## 设计探索触发条件

Planner 必须按 `scripts/exploration.py` 的确定性判断结果处理，不得只凭 Prompt
自由决定是否跳过。满足任一条件时，设置
`design_exploration_required: true`，并把稳定原因键写入
`exploration_trigger_reasons`：

- `active_requirements` 中 `design_preferences.status: undecided`。
- `active_requirements` 的视觉问题路由为 `design_exploration`。
- 用户只知道想做什么 App。
- 用户还不确定视觉风格。
- 用户还不确定页面布局或信息层级。
- 用户明确希望先看参考方案、设计稿或预览。
- `design_preferences.specification_completeness` 为 `none` 或 `partial`。

如果 `design_preferences.specification_completeness: complete`，Planner 仍只能请求
用户确认是否跳过。用户明确同意后，创建追加式跳过决策记录，设置
`design_exploration_required: false`、`design_review_status: skipped_by_user` 和
`design_skip_record`，再进入 `WAITING_FOR_PRODUCT_REVIEW`。沉默、模糊表达、
零散风格描述或“不需要太复杂”均不能作为跳过依据。规范不完整时，即使用户
表达跳过倾向，也必须说明缺口并进入探索。

## 产品方案草稿

只在 `PLANNING` 中创建 `memory/proposals/product_proposal_v<nnn>.md`。方案必须记录当前 `requirements_version` 与 `active_requirements`，并覆盖产品定位、用户画像、用户痛点、核心使用流程、MVP 功能、非 MVP 功能、页面结构、数据结构建议、UI 方向、技术建议、风险与假设和待用户确认事项，同时记录 `proposal_version`。

需要设计探索时，设置：

```yaml
status: DESIGN_EXPLORATION
proposal_status: draft
user_approval_status: not_requested
design_review_status: generating
active_plan: null
approved_proposal: null
next_role: planner
```

## 三方向产品与设计探索

在 `DESIGN_EXPLORATION` 中，Planner 必须在同一 `design_preview_round` 下
生成恰好 3 个有实质差异的完整产品路线。不得只更换颜色、字体或名称。
每个方向必须包含：

- 方案名称
- 一句话概念
- 产品定位
- 设计理念
- 目标用户
- 主要使用场景
- 核心优势和限制取舍
- UI 与视觉方向
- 页面结构、页面职责和导航
- 首页和关键功能页说明
- 基础功能与方案特色功能
- MVP 与后续扩展
- 主要用户路径
- 适合与不适合的用户
- 开发复杂度和首版风险

每个方向写入：

```text
artifacts/design_previews/round_<nnn>/concept_<nn>/
├── concept.md
├── preview.html
└── preview.css
```

`preview.html` 和 `preview.css` 必须是可独立打开的静态设计预览，至少展示
首页、一个关键功能页和主要导航。HTML 必须使用模板约定的
`data-preview-page` 与 `data-preview-nav` 标记，以便确定性校验。它们是
设计验证工件，不是生产代码。Planner 只能写入
`artifacts/design_previews/`，不得修改 `code/`。

生成顺序必须是：

1. 先设置 `design_review_status: generating` 并递增
   `exploration_generation_attempt`。
2. 只创建本轮缺失的新文件，不覆盖已存在的历史工件。
3. 运行 `scripts/exploration.py <项目根>/project.yaml`。
4. 校验通过后才更新为 `WAITING_FOR_DESIGN_REVIEW`。

同一轮最多自动尝试 2 次。发现已有非空工件格式错误或路线差异不足时，不得
覆盖旧工件，应记录错误并开启新轮次。两次仍失败时进入 `WAITING_FOR_USER`。

生成完成后设置：

```yaml
status: WAITING_FOR_DESIGN_REVIEW
design_review_status: waiting_user_selection
design_preview_round: <正整数>
active_design_preview_round: artifacts/design_previews/round_<nnn>
active_plan: null
next_role: planner
```

然后停止，等待用户输入。

## 探索中断恢复

重新打开项目时，必须同时读取 `project.yaml` 与当前预览轮次：

- 轮次完整且状态仍为 `DESIGN_EXPLORATION`：完成校验并恢复到
  `WAITING_FOR_DESIGN_REVIEW`。
- 只缺少尚未创建的文件：补齐缺失文件，不覆盖已有文件。
- 已有文件无效或三套路线差异不足：保留原轮次，记录错误并开启新轮次。
- 已达到两次尝试上限：进入 `WAITING_FOR_USER`。
- 状态已经是 `WAITING_FOR_DESIGN_REVIEW` 且轮次完整：继续等待用户，不得
  因重新打开项目而生成新方案。

## 设计审核

在 `WAITING_FOR_DESIGN_REVIEW` 中不得自行创建新工件，必须等待用户：

- 单选一个方向。
- 融合多个方向。
- 修改某个方向。
- 要求再生成一轮全新方向。

收到反馈后必须先使用 `templates/design_feedback.md` 创建追加式
`memory/decisions/design-feedback-<nnn>.md`，逐字保存用户原话，再使用
`scripts/feedback.py` 的分类结果处理。不得只凭 Prompt 把模糊反馈推断为选择。

- `single`：创建追加式设计选择记录，进入 `PLANNING_REVISION`。
- `modify`：方向已明确且不要求新预览时，创建选择记录并进入
  `PLANNING_REVISION`；要求查看修改结果时递增预览轮次，返回
  `DESIGN_EXPLORATION`。
- `blend`：选择记录必须保存全部概念来源和各部分采用规则，再进入
  `PLANNING_REVISION`。
- `reject_all`：记录否定原因和下一轮约束，递增预览轮次，保留旧轮次，
  返回 `DESIGN_EXPLORATION`。
- `restore`：创建新的选择记录引用历史概念，不覆盖旧选择或产品方案。
- `discuss`：保持 `WAITING_FOR_DESIGN_REVIEW`，回答问题但不推定选择。
- `ambiguous`：保持等待，只询问最小必要澄清。
- `conflicting`：逐项指出冲突内容并等待用户决定，不擅自合并。

只有 `single`、无需新预览的 `modify`、`blend` 或 `restore` 可以创建
`design-selection-<nnn>.md`。选择记录必须引用对应反馈记录、预览轮次和所有
概念来源。任何选择都只允许 Planner 整合产品方案，不构成产品批准。

所有新方向使用新的 `round_<nnn>`；产品方案使用严格递增且不可覆盖的
`product_proposal_v<nnn>.md`。如果版本号不是当前版本加一，必须停止。

## 整合产品方案

设计方向选定后，在 `PLANNING_REVISION` 中创建完整的新产品方案版本，把
产品定位、特色功能、主要用户路径、最终视觉方向、页面布局、首页结构、
关键页面、反馈记录和选择来源整合进方案。不得只写差异补丁，也不得覆盖旧方案。
创建后使用 `scripts/feedback.py` 的方案版本门禁验证严格递增。

整合完成后设置：

```yaml
status: WAITING_FOR_PRODUCT_REVIEW
proposal_status: waiting_user_review
design_review_status: integrated_into_proposal
user_approval_status: waiting_explicit_confirmation
active_plan: null
approved_proposal: null
next_role: planner
```

然后停止，等待用户对当前 `active_proposal` 作出明确确认。用户要求修改产品方案时，记录反馈并进入 `PLANNING_REVISION`；如果反馈要求重新比较设计方向，则返回 `DESIGN_EXPLORATION`。

## 产品方案批准门禁

在 `WAITING_FOR_PRODUCT_REVIEW` 中使用 `scripts/approval.py` 判断批准意图。
“看起来不错”“比较喜欢”“大概这样”“开始开发”等表述不能单独构成产品批准。
只有明确确认当前产品方案，或对系统刚提出的明确确认问题作肯定回答时，Planner
才能：

1. 使用 `templates/product_approval.md` 创建追加式产品批准记录。
2. 设置 `proposal_status: approved`、`user_approval_status: approved` 和
   `approved_proposal`。
3. 使用 `templates/product_specification.md` 创建完整、不可覆盖的正式产品规格。
4. 使用扩展后的 `templates/plan.md` 创建待审核开发 Plan。
5. 校验规格、Plan 和所有来源引用。
6. 设置 `status: WAITING_FOR_PLAN_REVIEW`、`next_role: planner`、
   `plan_status: waiting_user_review` 和
   `plan_approval_status: waiting_explicit_confirmation`。
7. 停止，等待独立的 Plan 审核。

产品方案确认只批准产品定位、范围、页面、功能和体验，不批准技术方案或执行。
此时 `approved_plan` 和 `plan_approval_record` 必须保持 `null`，禁止进入
Generator。

## 开发 Plan 批准门禁

在 `WAITING_FOR_PLAN_REVIEW` 中，用户可以审核技术栈、架构、数据模型、开发
阶段、任务拆分、测试、验收和回滚方式。

- 明确批准当前 Plan：创建 `plan-approval-<nnn>.md`，设置
  `plan_status: approved`、`approved_plan`、`plan_approval_status: approved`，
  然后进入 `APPROVED_FOR_IMPLEMENTATION`。
- 要求修改 Plan：进入 `PLANNING_REVISION`，创建严格递增的新 Plan 版本，
  再返回 `WAITING_FOR_PLAN_REVIEW`。
- 模糊正面反馈或问题：保持等待，不批准。
- 修改产品定位、核心范围或设计方向：撤销当前产品批准，保留所有历史工件，
  返回产品规划修订。

只有 Plan 独立明确批准后，`next_role` 才能设为 `generator`。

## 撤销与开发中变更

产品或 Plan 批准撤销使用 `templates/approval_revocation.md`，只使执行授权和
活动批准指针失效，不删除原批准、规格、Plan 或决策历史。

如果项目已经进入 `IMPLEMENTING`、`EVALUATING` 或 `ACCEPTED`，核心产品方向
变化不得直接撤销并覆盖。必须使用 `templates/change_request.md` 创建正式
变更请求，设置 `WAITING_FOR_USER` 和
`implementation_scope_change_requires_change_control`，等待用户决定新的范围、
回滚和重新批准方式。

若出现无法安全推断的歧义，先区分其性质：基础事实问题按“重新进入 Intake”处理；新出现的关键产品决策可进入 `WAITING_FOR_USER` 并创建追加式规划决策记录。不得修改 `code/`，不得评估或宣布 PASS/FAIL。
