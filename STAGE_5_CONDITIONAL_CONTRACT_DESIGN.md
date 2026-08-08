# Stage 5：Conditional Implementation Contract 设计

## 目标

只对高风险实现建立结构化 Contract，避免所有任务都被迫进入复杂 Sprint，同时让
高风险功能的完成条件、验证范围和回滚预期可复核。

## 确定性触发

规则由 `config/implementation_contract.yaml` 配置。以下任一条件成立即需要 Contract：

- 风险标签命中 migration、authentication、authorization、payment、destructive_operation、
  external_api、complex_state_machine、data_compatibility 或 high_risk_change_request。
- `irreversible: true`。
- 关键流程页数量达到阈值。
- Acceptance Criteria 数量达到阈值。

没有命中时维持普通路径 `Generator → Evaluator`，不创建 Contract 文件。

## Contract 内容与边界

高风险 Contract 位于 `memory/handoffs/implementation-contract-<nnn>.yaml`，记录功能、
已批准的 Requirements/AC、`done_when`、验证类型、回滚预期、风险和触发原因。

- `source_plan` 必须与 `approved_plan` 一致。
- Contract 中的 Requirements/AC 只能是批准来源的子集，不能借机新增需求。
- 高风险标签会要求对应的 persistence、integration、browser 或 regression 验证。
- Contract 不包含用户批准字段，不改变 `next_role`，不创建新的 Approval Gate。
- 每条 Contract 独占创建、严格递增、禁止覆盖；Approved Plan 永远优先。

## 验证

`scripts/implementation_contract.py` 负责风险判断、Contract 创建和校验；Stage 5 测试
覆盖普通任务不创建、各类高风险触发、强制验证、来源冲突、无批准新增和追加式保护。
