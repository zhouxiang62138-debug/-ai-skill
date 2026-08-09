# F9.3 受控重试治理阶段报告

## 1. 实施摘要

阶段结果：`PASS`。

新增增量复验选择、强制回归计划、稳定 Issue 根因身份、迭代趋势、重复失败、
回归、路由争议、提前升级、最大五次限制、Plan 新批准序列和追加式决策摘要。
同时将项目状态升级为 schema v6，并提供完整迁移与回滚工具。

## 2. 增量复验设计

针对性复验选择上轮 OPEN/REOPENED、Generator 声称
FIXED/PARTIALLY_FIXED/CANNOT_REPRODUCE、受改动文件影响的验收标准和相关测试。
其后始终合并 Profile 的 mandatory regression suite，不允许跳过。

## 3. 回归设计

之前为 PASS、当前为 FAIL 的 Requirement/Acceptance Criterion 创建新的
`regression` critical Issue，关联上次通过 Evaluation、当前证据和相关改动文件。
新回归不会被计入旧 Issue 的解决结果。

## 4. 趋势计算规则

每轮记录 previous/current 总数、blocker、critical、resolved、reopened、
repeated、new 和 regression。确定性状态为：

- `IMPROVING`：问题或高严重度减少且存在解决项；
- `REGRESSING`：出现回归、严重度增加或新增问题抵消解决项；
- `STALLED`：同一开放问题重复且无解决/新增；
- `STABLE`：无明确改善或恶化；
- `UNKNOWN`：没有可比较的上一轮。

## 5. 提前升级规则

默认阈值均为 2 轮：

- 同一 Issue 重复；
- Generator 重复声称修复但复验失败；
- 连续回归；
- 路由争议；
- 连续无进展。

路由争议升级 Planner；其余无效循环进入 `WAITING_FOR_USER`。升级后
`automatic_retry_allowed=false`，不得继续自动调用 Generator。

## 6. 最大迭代规则

只有 `FAIL + return_to=GENERATOR` 增加 `current_iteration`。BLOCKED、
WAITING_FOR_USER 和 Planner 路由不增加。第 5 次失败进入 `WAITING_FOR_USER`，
停止 Generator 并生成决策摘要。只有新的正式 Plan 和新的 Plan 批准记录同时有效，
才能增加 `iteration_sequence` 并将轮次归零；历史记录不清除。

## 7. 新增文件

- `scripts/evaluation_governance.py`
- `scripts/project_migration.py`
- `config/retry_governance.yaml`
- `config/schemas/iteration_metrics_v1.schema.json`
- `config/schemas/decision_summary_v1.schema.json`
- `config/schemas/project_v6.schema.json`
- `templates/decision_summary.yaml`
- `tests/test_evaluation_governance.py`
- `tests/test_project_migration.py`
- `F9_3_CONTROLLED_RETRY_GOVERNANCE_REPORT.md`

## 8. 修改文件

- `scripts/evaluation_protocol.py`
- `scripts/project_state.py`
- `config/workflow.yaml`
- `config/role_policies.yaml`
- `prompts/planner_prompt.md`
- `prompts/generator_prompt.md`
- `prompts/evaluator_prompt.md`
- `templates/project.yaml`
- `templates/project_skill_maintenance.yaml`
- `docs/F9_EVALUATION_PROTOCOL.md`
- `docs/project_state_schema.md`
- `docs/project_conventions.md`
- `SKILL.md`
- `tests/test_project_state.py`

## 9. Schema 和 Workflow 变化

Workflow v5 新增 `controlled_retry`。project schema v6 增加迭代序列、自动重试
授权、结构化工件指针、趋势、重复历史、路由争议、升级和决策摘要字段。
v3/v4/v5 继续可读；迁移工具支持 `check/preview/migrate/verify/rollback`。

## 10. 测试结果

F9.3 治理局部测试：34 通过，0 失败，0 跳过。

迁移局部测试：13 通过，0 失败，0 跳过。

完整回归：

```text
python -m unittest discover -s tests -v
```

结果：278 通过，0 失败，0 跳过。

## 11. 兼容性结果

- v3/v4/v5 读取测试继续通过；
- 归档项目迁移保持只读；
- 活跃 v3 实施/验收项目要求人工复核；
- v4/v5 使用加法迁移；
- 重复迁移 v6 幂等；
- 迁移和回滚均创建备份，拒绝覆盖现有备份；
- 旧 Issue、Evidence 和 Markdown 历史不删除。

## 12. 已知限制

- 根因签名依赖正式 Requirement/Acceptance Criterion 和稳定
  `root_cause_key`；旧 Issue 缺少该字段时使用确定性字段组合。
- 安装副本尚未同步，必须等最终系统审核通过。

## 13. 阶段完成标准逐项检查

- 增量复验选择：PASS
- mandatory regression 每轮执行：PASS
- stable Issue ID 与 REOPENED：PASS
- iteration metrics 五种趋势：PASS
- 重复 Issue 与 failed fix claim：PASS
- Regression 生成和追踪：PASS
- 路由争议及提前升级：PASS
- 最大五次限制：PASS
- 达到限制后停止 Generator：PASS
- 新 Plan 批准序列与防重置：PASS
- 决策摘要完整历史：PASS
- PASS/BLOCKED/WAITING/Planner 路由闭环：PASS
- schema v6 迁移、幂等和回滚：PASS
- 完整回归：PASS

## 14. 阶段审核结论

`PASS`

## 15. 是否允许执行最终系统审核

允许，自动进入最终系统审核。

## 16. 工作副本与安装副本差异

安装副本在本阶段保持只读。最终审核将重新计算差异、识别独立安装修改、验证真实
符号链接安全能力，并在全部 Gate PASS 后决定是否可安全同步。
