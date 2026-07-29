# Planner 设计探索与人工确认流程

## 状态机

`INTAKE → WAITING_FOR_REQUIREMENTS → PLANNING → DESIGN_EXPLORATION → WAITING_FOR_DESIGN_REVIEW → PLANNING_REVISION → WAITING_FOR_PRODUCT_REVIEW → WAITING_FOR_PLAN_REVIEW → APPROVED_FOR_IMPLEMENTATION → IMPLEMENTING`

`WAITING_FOR_PLAN_REVIEW` 表示产品方案已批准，正式产品规格和开发 Plan 已生成，
但 Plan 尚未批准。`APPROVED_FOR_IMPLEMENTATION` 表示产品方案和当前 Plan
均已分别明确批准，Generator 可以开始门禁校验。

| 状态 | 进入条件 | 当前负责人 | 允许操作 | 输出文件 | 下一状态 |
| --- | --- | --- | --- | --- | --- |
| `INTAKE` | 收到初始需求 | First-Ask Intake | 记录原始请求、采访和需求快照 | `memory/requirements/` | `WAITING_FOR_REQUIREMENTS` 或 `PLANNING` |
| `WAITING_FOR_REQUIREMENTS` | 信息不足 | 用户；回复后恢复 First-Ask | 回答本轮 1～3 个问题 | 新采访和需求快照版本 | `INTAKE` 或 `PLANNING` |
| `PLANNING` | `active_requirements` 有效且足以规划 | Planner | 创建产品方案草稿并判断设计探索是否必需；禁止创建正式计划 | `memory/proposals/product_proposal_v001.md` | `DESIGN_EXPLORATION`；用户明确同意跳过时可进入 `WAITING_FOR_PRODUCT_REVIEW` |
| `DESIGN_EXPLORATION` | 产品或设计方向未确定 | Planner | 同一轮生成 3 个有实质差异的完整产品路线及静态预览，并执行确定性校验 | `artifacts/design_previews/round_<nnn>/` | `WAITING_FOR_DESIGN_REVIEW` |
| `WAITING_FOR_DESIGN_REVIEW` | 已生成 3 个方向 | 用户 | 单选、融合、修改、全部否定、讨论或恢复旧方向 | 每次先创建 `design-feedback-<nnn>.md`；明确选择时再创建 `design-selection-<nnn>.md` | `PLANNING_REVISION`、`DESIGN_EXPLORATION` 或保持等待 |
| `WAITING_FOR_PRODUCT_REVIEW` | 已生成整合方案 | 用户 | 审核、明确确认或提出修改意见 | 产品批准后生成批准记录、正式产品规格和待审核 Plan | `PLANNING_REVISION` 或 `WAITING_FOR_PLAN_REVIEW` |
| `WAITING_FOR_PLAN_REVIEW` | 产品已批准且待审核 Plan 完整 | 用户 | 审核技术方案、任务、测试、验收和回滚 | Plan 批准记录或严格递增的新 Plan 版本 | `PLANNING_REVISION` 或 `APPROVED_FOR_IMPLEMENTATION` |
| `PLANNING_REVISION` | 用户选择设计方向或提出修改 | Planner | 整合设计选择并创建完整的新方案版本 | `memory/proposals/product_proposal_v<nnn>.md` | `WAITING_FOR_PRODUCT_REVIEW`；需要重新探索时进入 `DESIGN_EXPLORATION` |
| `APPROVED_FOR_IMPLEMENTATION` | 用户分别批准产品方案和当前开发 Plan | Generator | 校验完整来源链 | 产品批准、产品规格、获批 Plan 和 Plan 批准记录均存在 | `IMPLEMENTING` |
| `IMPLEMENTING` | 所有门禁通过 | Generator | 实施获批计划 | `code/`、`artifacts/`、交接记录 | `EVALUATING` |

## Planner 行为规则

收到简单需求（例如“我要做一个记账 App”）时，First-Ask 必须先创建结构化需求快照。只有 `requirements_status: sufficient_for_planning` 且 `active_requirements` 有效后，Planner 才能使用 `templates/product_proposal.md` 创建 `memory/proposals/product_proposal_v001.md`。如果需求快照把视觉偏好标记为 `undecided` 或设计规范不完整，Planner 必须生成一轮 3 套不同的完整产品路线。每个方向使用 `templates/design_concept.md`，并提供可独立打开的 `preview.html` 和 `preview.css`。

三个方向不能只是换色或换名称，必须在产品定位、核心优势、特色功能、主要
用户路径或信息架构中至少形成两项可验证差异。每个 `concept.md` 必须使用
`templates/design_concept.md` 的完整章节，覆盖产品路线、页面、功能、UI、
适用建议、复杂度和首版风险。

进入 `WAITING_FOR_DESIGN_REVIEW` 前必须运行
`scripts/exploration.py <项目根>/project.yaml`。生成最多自动尝试两次；
缺失文件可以在当前轮次补齐，已有无效文件不得覆盖，应保留原轮次并创建新轮次。

用户可以选择一个方案、融合多个方案、提出修改、否定全部方向、继续讨论或
恢复历史方向。Planner 每次先使用 `templates/design_feedback.md` 保存原话和
分类结果；只有明确选择才使用 `templates/design_selection.md`。需要重新比较
时递增 `design_preview_round`，创建新轮次并保留旧轮次。

反馈分类与状态迁移使用 `scripts/feedback.py`。讨论、含糊和冲突反馈不得生成
选择记录；恢复旧方向通过新的选择记录引用历史工件，不得回写旧记录。整合后的
产品方案版本必须严格递增一位，禁止覆盖或跳号。

选择设计方向只允许 Planner 整合新的产品方案版本。未经用户对整合后的
`active_proposal` 作出明确确认，严禁生成正式产品规格或待审核 Plan。

用户明确批准产品方案时，Planner 使用 `templates/product_approval.md` 创建
产品批准记录，再生成正式产品规格和待审核 Plan，进入
`WAITING_FOR_PLAN_REVIEW`。只有用户独立批准当前 Plan 后才能创建
`plan-approval-<nnn>.md` 并进入 Generator。

如果需求快照把设计规范标记为 `specification_completeness: complete`，Planner
仍须询问是否跳过 Design Exploration。只有用户明确同意后，才能使用
`templates/design_skip_decision.md` 创建追加式记录，设置
`design_exploration_required: false`、`design_review_status: skipped_by_user`
和 `design_skip_record`，再进入 `WAITING_FOR_PRODUCT_REVIEW`。规范为
`none` 或 `partial` 时不得跳过。跳过设计探索不等于批准产品方案。

## 用户确认机制

产品方案和开发 Plan 分别使用 `scripts/approval.py` 判断。仅当用户明确指出
当前批准目标，或对系统刚提出的明确确认问题作肯定回答时才能批准。“开始开发”
“看起来不错”“比较喜欢”等单独表达均不足以通过任何门禁。

确认前：

```yaml
status: WAITING_FOR_PRODUCT_REVIEW
requirements_status: sufficient_for_planning
active_requirements: memory/requirements/requirements_v001.yaml
proposal_status: waiting_user_review
user_approval_status: waiting_explicit_confirmation
active_proposal: memory/proposals/product_proposal_v002.md
approved_proposal: null
proposal_version: 2
design_exploration_required: true
design_review_status: integrated_into_proposal
design_preview_round: 1
active_design_preview_round: artifacts/design_previews/round_001
selected_design_concept:
  mode: blend
  concept_refs:
    - artifacts/design_previews/round_001/concept_01
    - artifacts/design_previews/round_001/concept_03
  integration_notes: 使用 concept_01 的布局与 concept_03 的图表风格
design_selection_record: memory/decisions/design-selection-001.md
design_skip_record: null
active_plan: null
next_role: planner
```

产品确认后、Plan 确认前：

```yaml
status: WAITING_FOR_PLAN_REVIEW
requirements_status: sufficient_for_planning
active_requirements: memory/requirements/requirements_v001.yaml
proposal_status: approved
user_approval_status: approved
active_proposal: memory/proposals/product_proposal_v002.md
approved_proposal: memory/proposals/product_proposal_v002.md
proposal_version: 2
design_exploration_required: true
design_review_status: integrated_into_proposal
design_selection_record: memory/decisions/design-selection-001.md
product_approval_record: memory/decisions/product-approval-001.md
product_spec_status: finalized
active_product_spec: memory/specifications/product_spec_v001.md
plan_status: waiting_user_review
active_plan: memory/plans/plan-001.md
approved_plan: null
plan_approval_status: waiting_explicit_confirmation
plan_approval_record: null
next_role: planner
```

Plan 独立确认后：

```yaml
status: APPROVED_FOR_IMPLEMENTATION
plan_status: approved
active_plan: memory/plans/plan-001.md
approved_plan: memory/plans/plan-001.md
plan_approval_status: approved
plan_approval_record: memory/decisions/plan-approval-001.md
next_role: generator
```

## 修改循环与文件结构

用户提出意见时，Planner 记录反馈并创建 `product_proposal_v002.md`、`product_proposal_v003.md` 等新文件；严禁覆盖已有版本。

```text
memory/
├── requirements/
│   ├── request-001.md
│   ├── interview-001.md
│   └── requirements_v001.yaml
├── proposals/
│   ├── product_proposal_v001.md
│   └── product_proposal_v002.md
├── plans/
│   └── plan-001.md
└── decisions/
    ├── design-selection-001.md
    └── product-approval-001.md

artifacts/
└── design_previews/
    └── round_001/
        ├── concept_01/
        │   ├── concept.md
        │   ├── preview.html
        │   └── preview.css
        ├── concept_02/
        │   ├── concept.md
        │   ├── preview.html
        │   └── preview.css
        └── concept_03/
            ├── concept.md
            ├── preview.html
            └── preview.css
```

## 实际交互示例

用户：“我要做一个记账 App。”

First-Ask：保存原始请求并生成 `requirements_v001.yaml`，进入 `PLANNING`。

Planner：生成 `product_proposal_v001.md` 草稿，再生成 `round_001` 的 3 个设计方向，进入 `WAITING_FOR_DESIGN_REVIEW` 并等待。

用户：“首页用方案一，统计页用方案三，但配色不要太暗。”

Planner：创建 `design-selection-001.md` 和整合后的 `product_proposal_v002.md`，进入 `WAITING_FOR_PRODUCT_REVIEW` 并等待。

用户：“确认整合后的方案。”

Planner：记录产品确认，生成正式产品规格和 `plan-001.md`，进入
`WAITING_FOR_PLAN_REVIEW`。

用户：“确认当前开发 Plan。”

Planner：创建 `plan-approval-001.md`，设置 `APPROVED_FOR_IMPLEMENTATION`
和 `next_role: generator`。
