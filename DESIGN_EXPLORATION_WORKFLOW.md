# Planner Design Exploration 工作流

> 文档状态：用户已确认，作为实施依据  
> 适用范围：AI Development Team Skill 的 Planner 流程升级  
> 本文描述已批准的设计探索规则，并与 First-Ask Intake 和 workflow/schema v7 保持一致。

## 1. 目标

当用户提出新的 App 需求，但尚未确定视觉风格、页面布局或具体设计方向时，Planner 不应直接生成正式开发计划。Planner 必须先创建产品方案草稿，再提供 3 个彼此有明显差异、可实际预览的设计方向，让用户选择、融合或要求重新探索。

设计探索的核心目的：

- 把模糊的审美偏好转化为可比较的设计方向。
- 在开发前确认信息架构、首页结构和关键页面。
- 让用户保留最终产品决策权。
- 防止未经设计确认的需求提前进入 Generator。

## 2. 触发条件

满足下列任一情况时，必须进入 Design Exploration：

- `active_requirements` 将视觉偏好标记为 `undecided` 并路由到 `design_exploration`。
- 用户只知道想做什么 App，但没有给出明确设计方向。
- 用户还不确定视觉风格。
- 用户还不确定页面布局或信息层级。
- 用户明确表示希望先看参考方案、设计稿或预览。

如果用户已经提供完整且无歧义的设计规范，Planner 可以在产品方案草稿中记录“无需设计探索”的理由，并请求用户确认是否跳过。只有用户明确同意跳过，才可直接进入产品方案审核；Planner 不得自行把沉默视为同意。

## 3. 主流程

```text
用户需求
  ↓
First-Ask 保存原始请求并生成结构化需求快照
  ↓
Planner 读取 active_requirements 并生成产品方案草稿
  ↓
Planner 生成同一轮次的 3 个轻量方向和一个共用比较页
  ↓
用户选择一个方案 / 融合多个方案 / 提出修改意见 / 要求新方向
  ↓
Planner 在新轮次只生成 1 个选定方向的高保真预览
  ↓
用户明确确认高保真预览
  ↓
Planner 根据确认结果整合设计方向并生成新版产品方案
  ↓
用户审核整合后的完整产品方案
  ↓
用户明确确认
  ↓
Planner 记录产品确认并生成正式产品规格与待审核 Plan
  ↓
用户独立审核并明确批准当前 Plan
  ↓
进入 Generator
```

设计方向选择、高保真确认和产品方案批准是三个不同事件：

1. 用户选择设计方向，只允许 Planner 为选中结果生成一套高保真预览。
2. 用户确认高保真预览，只表示 Planner 可以据此整合产品方案。
3. 用户确认整合后的完整产品方案，只允许 Planner 生成正式产品规格和待审核 Plan。
4. 用户独立批准当前 Plan，才允许进入 Generator。

## 4. 建议状态机

```text
INTAKE
  → WAITING_FOR_REQUIREMENTS（信息不足时）
  → PLANNING
  → DESIGN_EXPLORATION
  → WAITING_FOR_DESIGN_REVIEW
      ├─ 要求修改或新方向 → DESIGN_EXPLORATION
      ├─ 选择或融合方向 → DESIGN_EXPLORATION（单一高保真）
      └─ 确认单一高保真 → PLANNING_REVISION
  → WAITING_FOR_PRODUCT_REVIEW
      ├─ 要求修改 → PLANNING_REVISION
      └─ 明确确认 → WAITING_FOR_PLAN_REVIEW
  → WAITING_FOR_PLAN_REVIEW
      ├─ 要求修改 Plan → PLANNING_REVISION
      └─ 明确批准 Plan → APPROVED 事件
  → APPROVED_FOR_IMPLEMENTATION
  → IMPLEMENTING
```

| 状态 | 当前负责人 | 允许操作 | 必需输出 | 下一状态 |
| --- | --- | --- | --- | --- |
| `INTAKE` | First-Ask Intake | 保存原始请求、采访并创建需求快照 | `memory/requirements/` | `WAITING_FOR_REQUIREMENTS` 或 `PLANNING` |
| `WAITING_FOR_REQUIREMENTS` | 用户；回复后恢复 First-Ask | 回答本轮少量问题 | 新采访和需求快照版本 | `INTAKE` 或 `PLANNING` |
| `PLANNING` | Planner | 读取 `active_requirements`，生成产品方案草稿并判断是否需要设计探索 | `memory/proposals/product_proposal_v<nnn>.md` | `DESIGN_EXPLORATION`，或经用户明确同意后进入 `WAITING_FOR_PRODUCT_REVIEW` |
| `DESIGN_EXPLORATION` | Planner | 第一阶段生成 3 个轻量方向和共用比较页；选择后第二阶段只生成 1 个完整高保真预览 | 3 份方向 `concept.md` + 共用比较页，或 1 组 `selected_concept` 完整预览 | 校验通过后进入 `WAITING_FOR_DESIGN_REVIEW` |
| `WAITING_FOR_DESIGN_REVIEW` | 用户 | 比较、选择、融合、修改、确认原型或要求新方向 | 用户反馈记录；明确方向时追加选择记录 | 方向选择回到 `DESIGN_EXPLORATION`；原型确认进入 `PLANNING_REVISION` |
| `PLANNING_REVISION` | Planner | 整合已选方向和用户反馈，创建完整的新产品方案版本 | 新的 `product_proposal_v<nnn>.md` | `WAITING_FOR_PRODUCT_REVIEW`；若仍需重新探索则进入 `DESIGN_EXPLORATION` |
| `WAITING_FOR_PRODUCT_REVIEW` | 用户 | 审核整合后的产品范围和设计方向 | 产品批准记录、正式产品规格、待审核 Plan | `PLANNING_REVISION` 或 `WAITING_FOR_PLAN_REVIEW` |
| `WAITING_FOR_PLAN_REVIEW` | 用户 | 审核技术方案、任务、测试、验收与回滚 | Plan 批准记录或新版 Plan | `PLANNING_REVISION` 或 `APPROVED_FOR_IMPLEMENTATION` |
| `APPROVED_FOR_IMPLEMENTATION` | Generator | 校验需求、产品批准、正式规格、获批 Plan 与 Plan 批准来源链 | 可选交接记录 | `IMPLEMENTING` |

用户明确批准产品方案后，Planner 创建产品批准记录、正式规格和待审核 Plan，
持久化为 `WAITING_FOR_PLAN_REVIEW`。用户再明确批准当前 Plan 后，才持久化为
`APPROVED_FOR_IMPLEMENTATION`。

## 5. 三个方向的差异要求

每轮必须恰好输出 3 个轻量方向。三个方向不能只是换颜色或换名称，而应在产品
定位、核心优势、特色功能和主要用户路径四个维度中至少有两项不同；视觉语言、
信息密度、布局方式和交互重点也应服务于对应产品定位。完整产品路线和高保真
页面只在用户选定方向后的 `selected_prototype` 阶段生成。

可使用但不限于以下方向：

- 极简专业风：留白充足、结构克制、强调可信度和效率。
- 年轻活泼风：明快色彩、圆角卡片、轻量动效、强调亲和力。
- 深色数据仪表盘风：高信息密度、深色背景、图表优先、强调监控与洞察。

Planner 应根据产品场景调整方向。例如儿童产品、医疗工具和开发者控制台不应机械套用同一组风格。

## 6. 两阶段产物要求

第一阶段每个方向只要求方案名称、一句话概念、产品定位、核心优势、限制与
取舍、页面结构、特色功能和主要用户路径，使用 `templates/design_direction.md`。
三个方向共用一个 `comparison.html` 和 `comparison.css`，只做结构校验与批量
smoke check。

第二阶段只为选中或融合后的结果生成一个 `selected_concept`，使用
`templates/design_concept.md`。完整内容包括：

1. 方案名称
2. 一句话概念和产品定位
3. 设计理念、目标用户和主要使用场景
4. 核心优势、限制与取舍
5. UI 与视觉方向
6. 页面结构、页面职责和导航
7. 首页与关键功能页说明
8. 基础功能、特色功能、MVP 和后续扩展
9. 主要用户路径
10. 适用建议、开发复杂度和首版风险

建议补充：

- 字体与字号层级
- 圆角、阴影、间距等视觉规则
- 导航方式
- 核心交互说明
- 响应式策略
- 无障碍注意事项
- 与当前产品方案版本的关联

只有第二阶段的 `selected_concept/preview.html` 与 `preview.css` 才形成可独立
打开的静态预览，至少展示首页、一个关键功能页和关键导航关系。HTML 必须包含
`data-preview-page="home"`、`data-preview-page="key-feature"` 和
`data-preview-nav` 标记。它们属于设计验证工件，不是生产代码，不得放入
`code/`，也不得被 Generator 直接视为已批准实现。

进入等待状态前必须运行 `scripts/exploration.py <项目根>/project.yaml`。
生成最多自动尝试两次：中断且只有文件缺失时可补齐当前轮次；已有文件无效或
路线差异不足时必须保留原轮次并创建下一轮；两次仍失败则进入
`WAITING_FOR_USER`。

## 7. 建议产物结构

为了保留多轮探索历史，建议在用户给出的结构外增加轮次目录：

```text
memory/
├── requirements/
│   └── requirement-001.md
├── proposals/
│   ├── product_proposal_v001.md
│   └── product_proposal_v002.md
└── decisions/
    ├── design-selection-001.md
    └── product-approval-001.md

artifacts/
└── design_previews/
    ├── round_001/
    │   ├── concept_01/concept.md
    │   ├── concept_02/concept.md
    │   ├── concept_03/concept.md
    │   ├── comparison.html
    │   └── comparison.css
    └── round_002/
        └── selected_concept/
            ├── concept.md
            ├── preview.html
            └── preview.css
```

所有产品方案、设计预览、用户反馈和决策记录都必须追加保存，禁止覆盖旧文件。新一轮探索使用新的 `round_<nnn>`；同一轮中的概念编号固定为 `concept_01` 至 `concept_03`。

建议 `design-selection-<nnn>.md` 记录：

- 关联的产品方案版本
- 关联的预览轮次
- 关联的产品探索反馈记录
- 用户选择的单一方案或融合方案
- 用户原始反馈
- Planner 的整合摘要
- 选择时间

## 8. 用户可执行的操作

在 `WAITING_FOR_DESIGN_REVIEW` 中，用户可以：

- 直接选择一个方案，例如“选择 concept_02”。
- 要求融合多个方案，例如“使用 concept_01 的布局和 concept_03 的配色”。
- 对某个方案提出修改，例如“保留方案二，但减少动画和高饱和色”。
- 否定当前三种方向，要求 Planner 再生成一轮新的风格。
- 暂不选择并继续询问成本、适用人群或页面细节。
- 恢复以前轮次的某个方向。

每次反馈先创建 `design-feedback-<nnn>.md`，再按
`scripts/feedback.py` 的结构化结果处理：

- 单选：记录所选方案，增加轮次，只生成一套高保真预览。
- 融合：记录所有来源方案及融合规则，增加轮次，只生成融合后的高保真预览。
- 局部修改：记录修改约束，增加轮次，只生成修改后的高保真预览。
- 确认高保真预览：进入 `PLANNING_REVISION`，整合新版产品方案。
- 新方向：增加 `design_preview_round`，保留旧轮次，返回 `DESIGN_EXPLORATION`。
- 全部否定：记录否定原因和下一轮必须避免的内容，增加轮次。
- 继续讨论：保持 `WAITING_FOR_DESIGN_REVIEW`，不创建设计选择记录。
- 恢复旧方向：创建新的选择记录引用历史轮次，不覆盖任何旧工件。
- 表述含糊或冲突：保持 `WAITING_FOR_DESIGN_REVIEW`，只澄清未决部分。

## 9. 强制门禁

在用户尚未明确选择设计方向、尚未明确确认单一高保真预览，或尚未明确确认整合后的产品方案时：

- 禁止创建任何 `memory/plans/plan-<nnn>.md`。
- `active_plan` 必须为 `null`。
- `approved_proposal` 必须为 `null`。
- `next_role` 必须保持 `planner`。
- 禁止进入 `APPROVED_FOR_IMPLEMENTATION`、`IMPLEMENTING` 或 Generator。
- 禁止把“看起来不错”“可以参考”“先这样”等模糊表述当作批准。
- 禁止把“选择 concept_02”直接解释为“批准开发”。

只有用户对当前 `active_proposal` 给出无歧义确认，例如“确认整合后的产品方案”
或“批准当前产品方案”，Planner 才能完成第一道产品门禁：

1. 将 `proposal_status` 和 `user_approval_status` 更新为 `approved`。
2. 设置 `approved_proposal`。
3. 写入产品确认决策记录。
4. 生成正式 `product_spec_v<nnn>.md`。
5. 生成待审核 `plan-<nnn>.md`。
6. 设置 `status: WAITING_FOR_PLAN_REVIEW`、`next_role: planner`。

此时 `approved_plan` 和 `plan_approval_record` 必须保持 `null`。“开始开发”
不能单独构成产品或 Plan 批准。只有用户另行明确确认当前 `active_plan` 后，
Planner 才能写入 Plan 批准记录、设置 `approved_plan`，并进入
`APPROVED_FOR_IMPLEMENTATION` 交给 Generator。

## 10. `project.yaml` 字段

schema v7 使用以下相关字段（旧 v4 项目须先经过迁移）：

```yaml
schema_version: 7

# Intake 来源
requirements_status: sufficient_for_planning
requirements_version: 1
active_requirements: memory/requirements/requirements_v001.yaml

# 产品方案状态
proposal_status: draft
proposal_version: 1
active_proposal: memory/proposals/product_proposal_v001.md
approved_proposal: null
user_approval_status: not_requested
product_approval_record: null
approval_revocation_record: null
change_request_record: null

# 正式产品规格
product_spec_status: not_started
product_spec_version: 0
active_product_spec: null

# 设计探索状态
design_exploration_required: true
exploration_trigger_reasons:
  - visual_preferences_undecided
design_review_status: waiting_user_selection
design_preview_round: 1
active_design_preview_round: artifacts/design_previews/round_001
exploration_generation_attempt: 1
exploration_error_record: null
selected_design_concept: null
design_selection_record: null
design_skip_record: null
exploration_feedback_record: null
design_feedback_status: not_started
design_feedback_round: 0

# 正式计划门禁
plan_status: not_started
plan_version: 0
active_plan: null
approved_plan: null
plan_approval_status: not_requested
plan_approval_record: null
status: WAITING_FOR_DESIGN_REVIEW
next_role: planner
```

字段定义：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `proposal_version` | integer | 当前产品方案的数字版本；每次创建完整新版本后递增 |
| `design_exploration_required` | boolean | 当前需求是否必须经过设计探索；跳过时需有用户明确确认记录 |
| `design_review_status` | enum | 当前设计审核进度 |
| `design_preview_round` | integer | 最近一轮设计预览编号；新一轮生成前递增 |
| `active_design_preview_round` | string/null | 当前等待用户审核的预览轮次目录 |
| `selected_design_concept` | object/null | 单选或融合选择的结构化记录 |
| `design_selection_record` | string/null | 对应的追加式设计选择决策文件 |

建议 `design_review_status` 使用以下枚举：

- `not_started`
- `generating`
- `waiting_user_selection`
- `revision_requested`
- `direction_selected`
- `integrated_into_proposal`
- `skipped_by_user`

`selected_design_concept` 建议使用结构化值，以同时支持单选和融合：

```yaml
selected_design_concept:
  mode: blend
  concept_refs:
    - artifacts/design_previews/round_001/concept_01
    - artifacts/design_previews/round_001/concept_03
  integration_notes: 使用 concept_01 的布局与 concept_03 的配色和图表风格
```

单选时示例：

```yaml
selected_design_concept:
  mode: single
  concept_refs:
    - artifacts/design_previews/round_001/concept_02
  integration_notes: 保留整体方向，降低动画强度
```

字段约束：

- `design_review_status: waiting_user_selection` 时，`status` 必须为 `WAITING_FOR_DESIGN_REVIEW`，`next_role` 必须为 `planner`。
- `design_review_status: direction_selected` 只代表方向已选，不代表产品方案获批。
- `user_approval_status` 只有在设计方向已整合进当前产品方案后才能变为 `approved`。
- `WAITING_FOR_PLAN_REVIEW` 必须已有获批产品方案、产品批准记录、正式产品规格和待审核 Plan；`approved_plan` 必须仍为 `null`。
- `APPROVED_FOR_IMPLEMENTATION` 必须另有 Plan 批准记录，且 `approved_plan` 与 `active_plan` 指向同一版本。
- 设计探索必需时要求 `design_selection_record`；跳过时要求 `design_skip_record`。
- `proposal_version` 和文件名版本必须一致。
- `design_preview_round` 和 `active_design_preview_round` 中的轮次必须一致。

## 11. `AGENTS.md` 调整建议

建议在“产品确认规则”中增加以下硬性规则：

- 当需求符合 Design Exploration 触发条件时，Planner 必须在产品方案草稿后先生成同一轮 3 个轻量方向和一个共用比较页；用户选择后再生成唯一的高保真原型。
- Planner 在 `DESIGN_EXPLORATION` 中只允许写入 `artifacts/design_previews/`；不得借此修改 `code/`。
- 第一阶段方向只需包含规定的比较说明；可独立打开的静态预览只属于第二阶段的 `selected_concept`。
- 设计预览、用户反馈、设计选择记录和新版产品方案均为追加式历史工件，禁止覆盖。
- 用户可以单选、融合、修改或要求新一轮方向；Planner 必须按选择创建决策记录。
- 用户选择设计方向不等于批准产品方案。
- 未明确选择设计方向、未明确确认单一高保真预览或未明确确认整合后的产品方案时，禁止生成正式计划，禁止进入 Generator。
- 如果用户明确同意跳过设计探索，必须记录跳过决定；不得由 Planner 自行跳过。
- Generator 不得读取未选中的设计预览作为需求来源；只能依据 `approved_proposal`、正式计划和对应的设计选择记录实施。

同时应保留现有边界：

- 角色仍然只有 Planner、Generator、Evaluator。
- Planner 不得修改 `code/`，不得宣布 PASS 或 FAIL。
- Generator 不得批准产品或设计方向。
- Evaluator 不得修改设计选择、产品方案或正式计划。

## 12. `planner_prompt.md` 调整建议

建议把现有“产品确认优先规则”扩展为以下阶段化指令：

### 12.1 需求判断

Planner 读取 `project.yaml` 和当前需求后，先判断是否命中设计探索触发条件，并把结果写入 `design_exploration_required`。如果用户已经给出完整设计规范，也只能请求用户确认是否跳过，不能自行跳过。

### 12.2 产品方案草稿

先创建 `product_proposal_v<nnn>.md` 草稿，覆盖产品定位、用户画像、用户痛点、核心功能、页面结构、UI 初步方向、技术建议、MVP 范围和后续扩展。此时不得创建正式计划。

### 12.3 两阶段预览

进入 `DESIGN_EXPLORATION` 后，先生成同轮次 3 个有实质差异的轻量方向和一个共用比较页：

- `concept.md`
- `comparison.html`
- `comparison.css`

生成完成后设置：

```yaml
status: WAITING_FOR_DESIGN_REVIEW
design_review_status: waiting_user_selection
active_design_preview_round: artifacts/design_previews/round_<nnn>
next_role: planner
```

然后停止，等待用户选择方向。选择后创建下一轮，只生成
`selected_concept/concept.md`、`preview.html` 和 `preview.css`，完成唯一原型的
完整 Browser QA，再回到 `WAITING_FOR_DESIGN_REVIEW` 等待高保真确认。

### 12.4 用户反馈处理

- 用户单选或融合：创建 `design-selection-<nnn>.md`，设置 `direction_selected`，递增轮次，进入唯一 `selected_prototype` 生成。
- 用户确认高保真预览：进入 `PLANNING_REVISION`，再整合新产品方案。
- 用户要求调整或新方向：记录反馈，递增 `design_preview_round`，返回 `DESIGN_EXPLORATION`；只有全部否定才重新生成 3 个方向。
- 用户表达含糊：保持 `WAITING_FOR_DESIGN_REVIEW`，不得推定选择。

### 12.5 整合与最终确认

Planner 根据选择创建新的完整产品方案版本，把最终视觉方向、布局、首页结构和关键页面写入方案。设置：

```yaml
status: WAITING_FOR_PRODUCT_REVIEW
design_review_status: integrated_into_proposal
user_approval_status: waiting_explicit_confirmation
next_role: planner
```

然后停止，等待用户对当前 `active_proposal` 作出明确确认。

### 12.6 双重批准门禁

Prompt 中应明确区分：

- “选择设计方向”不是开发批准。
- 只有“确认当前整合方案”才是产品批准。
- 产品批准前，禁止创建正式产品规格和 Plan，禁止设置 `approved_proposal`。
- 产品批准后，创建正式产品规格和待审核 Plan，进入 `WAITING_FOR_PLAN_REVIEW`；
  此时仍禁止把 `next_role` 改为 `generator`。
- 只有用户另行明确确认当前 Plan，才能设置 `approved_plan` 并交给 Generator。
- 产品或 Plan 批准在实施前被撤销时，必须创建追加式撤销记录并返回
  `PLANNING_REVISION`；实施开始后的范围变化必须进入正式变更控制。

## 13. 其他配套文件的后续调整范围

以下文件共同构成当前协议的实现与验证面，修改 Design Exploration 时必须同步检查：

- `SKILL.md`：在产品确认流程资源说明中加入 Design Exploration。
- `docs/PLANNER_APPROVAL_WORKFLOW.md`：扩展状态机、人工确认门禁和交互示例。
- `docs/workflow_protocol.md`：加入 `DESIGN_EXPLORATION`、`WAITING_FOR_DESIGN_REVIEW` 的角色路由。
- `config/workflow.yaml`：声明两阶段工件、Browser QA 和 Legacy 兼容范围。
- `config/role_policies.yaml`：允许 Planner 仅写 `artifacts/design_previews/`，允许 Generator 读取已批准的设计选择记录和所选预览。
- `templates/project.yaml`：升级 schema 并加入设计状态字段。
- `templates/product_proposal.md`：加入设计探索关联、所选设计方向和整合说明。
- `templates/design_direction.md`：第一阶段轻量方向模板。
- `templates/design_concept.md`：第二阶段高保真原型模板。

这些文件必须保持与本 Canonical Protocol 一致；它们不是额外的 Agent 或批准 Gate。

## 14. 示例

用户：“我要做一个个人记账 App，但不知道该做成什么风格。”

Planner：

1. 生成 `product_proposal_v001.md` 草稿。
2. 生成 `round_001` 的轻量方向比较：
   - `concept_01`：极简专业风
   - `concept_02`：年轻活泼风
   - `concept_03`：深色数据仪表盘风
3. 生成共用 `comparison.html` / `comparison.css`，执行批量 smoke check，进入
   `WAITING_FOR_DESIGN_REVIEW` 并等待。

用户：“首页用方案一，但统计页想用方案三，配色不要太暗。”

Planner：

1. 创建 `design-feedback-001.md` 和 `design-selection-001.md`，记录融合选择。
2. 生成 `round_002/selected_concept/` 的唯一高保真预览并完成 Browser QA。
3. 进入 `WAITING_FOR_DESIGN_REVIEW`，等待用户确认高保真预览。

用户：“确认这个高保真设计。”

Planner：

1. 创建整合后的 `product_proposal_v002.md`。
2. 进入 `WAITING_FOR_PRODUCT_REVIEW` 并等待产品方案确认。

用户：“确认整合后的产品方案。”

Planner：

1. 创建 `product-approval-001.md`。
2. 设置 `approved_proposal: memory/proposals/product_proposal_v002.md`。
3. 生成 `memory/specifications/product_spec_v001.md`。
4. 生成待审核的 `memory/plans/plan-001.md`。
5. 设置 `status: WAITING_FOR_PLAN_REVIEW`、`next_role: planner`，等待第二次确认。

用户：“确认当前开发 Plan。”

Planner：

1. 创建 `plan-approval-001.md`。
2. 设置 `approved_plan: memory/plans/plan-001.md`。
3. 设置 `status: APPROVED_FOR_IMPLEMENTATION`、`next_role: generator`。

## 15. 实施状态

本流程已由用户明确确认。状态机、角色规则、Prompt、项目模板与设计模板应以本文件为实施依据，并保持跨文件一致。
