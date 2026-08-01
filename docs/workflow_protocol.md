# 文件驱动调度协议

## Workflow v7：Durable Session Runtime

v7 不改变业务状态机，而是在其外围增加持久化 Session Store 和确定性
Orchestrator。Orchestrator 读取 `active_module`、`status`、`next_role` 后，只能
选择 First-Ask Module、Planner、Generator、Evaluator 或 WAIT。等待态、BLOCKED、
ACCEPTED 和 ARCHIVED 永不启动角色。

schema v7 的每次业务状态提交必须持有 Worker Lease，并以 `expected_revision`
执行 CAS。提交请求、冲突、成功和恢复写入追加式 Runtime Event；完整事件不写入
`project.yaml`。详细顺序见 `SESSION_EVENT_PROTOCOL.md`、
`ORCHESTRATOR_PROTOCOL.md` 和 `RECOVERY_PROTOCOL.md`。

## Workflow v6：Completed Project Change Request

`ACCEPTED/ARCHIVED → CHANGE_REQUESTED → WAITING_FOR_CHANGE_APPROVAL →
IMPLEMENTING → EVALUATING → RELEASE_READY → ACCEPTED`。`project.yaml` 是
唯一项目状态源；Change Request 文件只保存请求事实和追加式生命周期。完整 Gate
与恢复协议见 `COMPLETED_PROJECT_CHANGE_REQUEST_WORKFLOW.md`。

## Workflow v5 兼容层：结构化验收返工

Workflow v5 保留 Markdown 验收报告，并新增机器可读
`evaluation/issues/evaluation-<nnn>.yaml`。Evaluator 使用
`config/evaluation_protocol.yaml` 的枚举、路由和硬 PASS 条件；Generator 在
`memory/handoffs/responses/` 对每个 blocking/critical Issue 逐项回应。

路由优先级固定为 `SYSTEM_OR_USER > USER > PLANNER > GENERATOR > ACCEPTED`。
`project.yaml` 必须在 Issue Package 与 Markdown 报告均校验并提交后最后更新。
状态提交失败时，`evaluation/.transactions/<evaluation-id>/journal.json` 保留
`RECOVERY_REQUIRED`，不得把半完成事务当作新状态。

每次由 Codex 主动读取项目根目录唯一的 `project.yaml`。如果 `active_module: first_ask_intake`，读取 `intake/first_ask.md`；否则根据 `status` 和 `next_role` 选择 Planner、Generator 或 Evaluator。First-Ask 是前置 Module，不是第四个 Agent。YAML 只记录协议和状态，不会自行调度或执行。

`INTAKE` 和 `WAITING_FOR_REQUIREMENTS` 由 First-Ask Intake Module 处理。需求达到 `sufficient_for_planning` 且 `active_requirements` 有效后，进入 `PLANNING`。

`PLANNING`、`DESIGN_EXPLORATION` 和 `PLANNING_REVISION` 对应 Planner。
`WAITING_FOR_DESIGN_REVIEW`、`WAITING_FOR_PRODUCT_REVIEW` 与
`WAITING_FOR_PLAN_REVIEW` 是强制等待状态；即使 `next_role: planner`，
也必须等到用户输入后才能恢复。

进入 `DESIGN_EXPLORATION` 前必须使用确定性触发规则。设计规范为 `none` 或
`partial`、视觉偏好为 `undecided`、需求路由到探索或用户明确请求预览时，
必须探索。只有设计规范为 `complete` 且用户明确同意时才能跳过。

每轮必须生成恰好三套完整产品路线，并在状态进入
`WAITING_FOR_DESIGN_REVIEW` 前通过结构、预览标记和路线差异校验。生成中断时
只补齐缺失工件；已有无效工件不得覆盖。自动生成最多尝试两次，超过后进入
`WAITING_FOR_USER`。

在 `WAITING_FOR_DESIGN_REVIEW` 中，每条用户反馈都先写入追加式
`design-feedback-<nnn>.md`。明确单选、修改、融合或恢复旧方向后，Planner
创建追加式设计选择记录并进入 `PLANNING_REVISION`；全部否定或要求查看修改
预览时，递增 `design_preview_round` 并返回 `DESIGN_EXPLORATION`；讨论、
含糊或冲突反馈保持等待。设计方向选择不构成开发批准。

在 `WAITING_FOR_PRODUCT_REVIEW` 中，只有用户对当前 `active_proposal` 给出
明确确认，Planner 才可把产品批准状态更新为 `approved`，创建产品批准记录、
正式产品规格和待审核 Plan，然后进入 `WAITING_FOR_PLAN_REVIEW`。产品批准
不允许直接进入 Generator。

在 `WAITING_FOR_PLAN_REVIEW` 中，只有用户明确批准当前 `active_plan`，
Planner 才创建 Plan 批准记录、设置 `approved_plan` 并进入
`APPROVED_FOR_IMPLEMENTATION`。Plan 修改必须创建新版本并重新等待；产品范围
变化必须撤销产品批准并返回规划修订。

Generator 在 `APPROVED_FOR_IMPLEMENTATION` 只执行门禁校验。它必须验证
`active_requirements`、`approved_proposal`、设计选择或跳过记录、
`product_approval_record`、`active_product_spec`、`approved_plan` 与
`plan_approval_record` 的完整来源链。全部通过后进入 `IMPLEMENTING`；
任一门禁缺失时进入 `WAITING_FOR_USER`，不得实施。

Evaluator 对可返工 FAIL 递增 `current_iteration` 并路由；达到 5 时，必须写入 `status: WAITING_FOR_USER`、`next_role: null` 和 `blocked_reason: maximum_iterations_reached`。

旧状态只读迁移：

```text
DESIGN_REVIEW      → WAITING_FOR_DESIGN_REVIEW
PRODUCT_REVIEW     → WAITING_FOR_PRODUCT_REVIEW
PLANNING_COMPLETE  → APPROVED_FOR_IMPLEMENTATION
```

新项目不得写入旧状态。
