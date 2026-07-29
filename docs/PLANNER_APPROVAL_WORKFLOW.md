# Planner 设计探索与人工确认流程

## 状态机

`INTAKE → WAITING_FOR_REQUIREMENTS → PLANNING → DESIGN_EXPLORATION → WAITING_FOR_DESIGN_REVIEW → PLANNING_REVISION → WAITING_FOR_PRODUCT_REVIEW → APPROVED_FOR_IMPLEMENTATION → IMPLEMENTING`

`APPROVED_FOR_IMPLEMENTATION` 表示用户已明确批准当前产品方案，正式计划已经生成，Generator 可以开始门禁校验。

| 状态 | 进入条件 | 当前负责人 | 允许操作 | 输出文件 | 下一状态 |
| --- | --- | --- | --- | --- | --- |
| `INTAKE` | 收到初始需求 | First-Ask Intake | 记录原始请求、采访和需求快照 | `memory/requirements/` | `WAITING_FOR_REQUIREMENTS` 或 `PLANNING` |
| `WAITING_FOR_REQUIREMENTS` | 信息不足 | 用户；回复后恢复 First-Ask | 回答本轮 1～3 个问题 | 新采访和需求快照版本 | `INTAKE` 或 `PLANNING` |
| `PLANNING` | `active_requirements` 有效且足以规划 | Planner | 创建产品方案草稿并判断设计探索是否必需；禁止创建正式计划 | `memory/proposals/product_proposal_v001.md` | `DESIGN_EXPLORATION`；用户明确同意跳过时可进入 `WAITING_FOR_PRODUCT_REVIEW` |
| `DESIGN_EXPLORATION` | 设计方向未确定 | Planner | 同一轮生成 3 个有实质差异的静态预览 | `artifacts/design_previews/round_<nnn>/` | `WAITING_FOR_DESIGN_REVIEW` |
| `WAITING_FOR_DESIGN_REVIEW` | 已生成 3 个方向 | 用户 | 单选、融合、修改或要求新一轮方向 | `memory/decisions/design-selection-<nnn>.md` 或反馈记录 | `PLANNING_REVISION` 或 `DESIGN_EXPLORATION` |
| `WAITING_FOR_PRODUCT_REVIEW` | 已生成整合方案 | 用户 | 审核、明确确认或提出修改意见 | 无；等待用户决定 | `PLANNING_REVISION` 或 `APPROVED_FOR_IMPLEMENTATION` |
| `PLANNING_REVISION` | 用户选择设计方向或提出修改 | Planner | 整合设计选择并创建完整的新方案版本 | `memory/proposals/product_proposal_v<nnn>.md` | `WAITING_FOR_PRODUCT_REVIEW`；需要重新探索时进入 `DESIGN_EXPLORATION` |
| `APPROVED_FOR_IMPLEMENTATION` | 用户明确确认当前方案 | Generator | 校验完整来源链 | `memory/plans/plan-001.md`、`memory/decisions/product-approval-001.md` 已存在 | `IMPLEMENTING` |
| `IMPLEMENTING` | 所有门禁通过 | Generator | 实施获批计划 | `code/`、`artifacts/`、交接记录 | `EVALUATING` |

## Planner 行为规则

收到简单需求（例如“我要做一个记账 App”）时，First-Ask 必须先创建结构化需求快照。只有 `requirements_status: sufficient_for_planning` 且 `active_requirements` 有效后，Planner 才能使用 `templates/product_proposal.md` 创建 `memory/proposals/product_proposal_v001.md`。如果需求快照把视觉偏好标记为 `undecided`，Planner 必须生成一轮 3 个不同设计方向。每个方向使用 `templates/design_concept.md`，并提供可独立打开的 `preview.html` 和 `preview.css`。

三个方向不能只是换色或换名称，必须在视觉语言、信息密度、布局方式、交互重点或目标用户感受上形成明显差异。每个 `concept.md` 至少包含方案名称、设计理念、目标用户、主色与辅助色、页面布局特点、首页结构、关键页面、优点和缺点。

用户可以选择一个方案、融合多个方案、提出修改或要求全新方向。Planner 使用 `templates/design_selection.md` 记录明确选择；需要重新比较时递增 `design_preview_round`，创建新轮次并保留旧轮次。

选择设计方向只允许 Planner 整合新的产品方案版本。未经用户对整合后的 `active_proposal` 作出明确确认，严禁生成 `plan.md` 或进入 Generator。

用户明确批准时，Planner 使用 `templates/product_approval.md` 创建产品批准记录，再生成正式计划。批准记录和计划必须同时引用当前需求快照、获批产品方案以及设计选择或跳过记录。

如果用户已经提供完整且无歧义的设计规范，Planner 仍须询问是否跳过 Design Exploration。只有用户明确同意后，才能使用 `templates/design_skip_decision.md` 创建追加式记录，设置 `design_exploration_required: false`、`design_review_status: skipped_by_user` 和 `design_skip_record`，再进入 `WAITING_FOR_PRODUCT_REVIEW`。跳过设计探索不等于批准产品方案。

## 用户确认机制

仅当用户对当前方案作出无歧义的确认时，才能批准。可接受示例包括“确认方案”“开始开发”“批准”“按这个方案做”。无法判断时，继续等待用户确认。

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

确认后：

```yaml
status: APPROVED_FOR_IMPLEMENTATION
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
active_plan: memory/plans/plan-001.md
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

Planner：记录确认，生成 `plan-001.md`，设置 `APPROVED_FOR_IMPLEMENTATION` 和 `next_role: generator`。
