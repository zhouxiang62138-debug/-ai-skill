# Stage 6：Context Rollover / Fresh Invocation 设计

## 目标

保持 F13 Context Builder、预算、Hash、增量 Resume 不变，在 F10 Durable Session 外增加
独立的 Model Invocation 生命周期。一个 Runtime Session 可以包含多个 Invocation；
Invocation 达到确定性阈值时，Runtime 追加 Handoff 并启动新的 Invocation。

## 确定性触发

`config/context.yaml` 的 `rollover` 配置控制：Context 预算百分比、Tool Call 数、
Compaction 数、运行时间、Phase Transition 和 Role Transition。Runtime 根据这些可观测
指标判断，不接受模型自报“上下文太长”作为唯一依据。

## Handoff 与持久化

Handoff 包含 `completed`、`current_state`、`files_changed`、`verification_completed`、
`open_issues`、`known_failures`、`next_actions`、`important_decisions`、
`do_not_repeat` 和 `references`。它只保存摘要和引用，不保存完整 Context 正文；
SQLite 中的 Handoff 表通过数据库触发器保持追加式，事件只保存 ID、原因和引用数量。

## Fresh Invocation

旧 Invocation 必须先进入 `ROLLED_OVER` 并拥有 Handoff。新 Invocation 使用：

```text
F13 Context Builder
+ Rollover Handoff
+ 当前 Durable project state
```

然后建立 `previous_invocation_id` 链接。缺少 Handoff、跨 Session、角色不一致或旧
Invocation 未 rollover 时拒绝恢复。整个过程不创建新的 Agent。
