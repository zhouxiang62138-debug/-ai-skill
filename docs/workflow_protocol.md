# 文件驱动调度协议

每次由 Codex 主动读取项目根目录唯一的 `project.yaml`。如果 `active_module: first_ask_intake`，读取 `intake/first_ask.md`；否则根据 `status` 和 `next_role` 选择 Planner、Generator 或 Evaluator。First-Ask 是前置 Module，不是第四个 Agent。YAML 只记录协议和状态，不会自行调度或执行。

`INTAKE` 和 `WAITING_FOR_REQUIREMENTS` 由 First-Ask Intake Module 处理。需求达到 `sufficient_for_planning` 且 `active_requirements` 有效后，进入 `PLANNING`。

`PLANNING`、`DESIGN_EXPLORATION` 和 `PLANNING_REVISION` 对应 Planner。`WAITING_FOR_DESIGN_REVIEW` 与 `WAITING_FOR_PRODUCT_REVIEW` 是强制等待状态；即使 `next_role: planner`，也必须等到用户输入后才能恢复。

在 `WAITING_FOR_DESIGN_REVIEW` 中，用户明确单选或融合后，Planner 创建追加式设计选择记录并进入 `PLANNING_REVISION`；用户要求新的比较方向时，递增 `design_preview_round` 并返回 `DESIGN_EXPLORATION`。设计方向选择不构成开发批准。

在 `WAITING_FOR_PRODUCT_REVIEW` 中，只有用户对当前 `active_proposal` 给出明确确认，Planner 才可把 `proposal_status` 和 `user_approval_status` 更新为 `approved`，写入 `approved_proposal` 与 `product_approval_record`，创建正式计划并进入 `APPROVED_FOR_IMPLEMENTATION`。用户要求修改时进入 `PLANNING_REVISION`；如果修改涉及重新比较风格，则返回 `DESIGN_EXPLORATION`。

Generator 在 `APPROVED_FOR_IMPLEMENTATION` 只执行门禁校验。它必须验证 `active_requirements`、`approved_proposal`、设计选择或跳过记录、`product_approval_record` 与 `active_plan` 的来源链。全部通过后进入 `IMPLEMENTING`；任一门禁缺失时进入 `WAITING_FOR_USER`，不得实施。

Evaluator 对可返工 FAIL 递增 `current_iteration` 并路由；达到 5 时，必须写入 `status: WAITING_FOR_USER`、`next_role: null` 和 `blocked_reason: maximum_iterations_reached`。

旧状态只读迁移：

```text
DESIGN_REVIEW      → WAITING_FOR_DESIGN_REVIEW
PRODUCT_REVIEW     → WAITING_FOR_PRODUCT_REVIEW
PLANNING_COMPLETE  → APPROVED_FOR_IMPLEMENTATION
```

新项目不得写入旧状态。
