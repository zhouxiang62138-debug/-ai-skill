# Stage 4：Planner WHAT/WHY 与 Generator HOW 设计

## 目标

把产品决策与实现决策分开：Planner 说明要解决什么问题、为什么做、范围到哪里；
Generator 在 `approved_plan` 约束内说明如何实现。Implementation Strategy 采用
追加式记录，任何新方案都创建新编号，不覆盖历史记录。

## 角色边界

### Planner

- 负责用户结果、业务范围、明确的非目标、需求与 Acceptance Criteria、产品约束、
  理由和产品风险。
- 可以记录技术约束，但不能把类名、函数名、目录结构、算法、具体组件拆分或实现
  步骤写成 Generator 必须照做的实现方案。
- 正式 Plan 和第一条 `planner_what_why` 记录都必须引用当前需求快照及 Plan。

### Generator

- 只能以获批 `approved_plan` 为唯一执行范围，并在此基础上追加
  `generator_how` 记录。
- 记录实现路线、文件变更、接口、执行顺序、测试命令、回滚方式和实现风险。
- 如果 HOW 与批准范围、需求或验收标准冲突，必须停在等待用户/变更控制，不得自行
  改写 WHAT/WHY 或扩大范围。

## 追加式记录协议

记录位于项目的 `memory/handoffs/implementation-strategy-<nnn>.yaml`：

1. 第 001 条必须是 `planner_what_why`。
2. 后续记录只能是 `generator_how`，通过 `parent_strategy_id` 串联。
3. 序号必须连续，文件已存在时写入失败，禁止覆盖。
4. 所有记录必须引用同一个 `source_plan`；校验时可传入 `approved_plan` 做一致性检查。
5. 该记录只解释执行策略，不能替代 `approved_plan`，也不能改变需求、验收标准或
   Evaluator 的评分阈值。

## 验证

`scripts/implementation_strategy.py` 提供记录创建、追加和链校验；Stage 4 测试覆盖
首条记录、非法低层 Planner 字段、Generator 追加、来源冲突和覆盖保护。
