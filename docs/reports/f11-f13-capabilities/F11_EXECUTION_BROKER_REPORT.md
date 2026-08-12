# F11.1 Execution Contract + ExecutionBroker 报告

## 实现内容

- 新增 `runtime/execution/`：ExecutionEnvironment Contract、ExecutionContext、ExecutionRequest、ExecutionResult、ExecutionReceipt、ExecutionBroker 和基于 `config/role_policies.yaml` 的路径策略。
- Broker 独占 F10 Runtime 交互：校验 Session、Role Run、Worker、Lease，创建/开始/完成 Tool Call，并由 `complete_tool_call()` 作为唯一 terminal Event 路径。
- logical_call_id 作为逻辑调用身份；相同身份重放复用 Tool Call 和不可变结果，不同身份即使 argv/cwd 相同也创建新调用。
- Broker 强制正式 code_snapshot_hash、environment_hash，并复用 F10 Attempt 与 result_hash。
- 禁止 Hands 直接写 `project.yaml`；Planner/Evaluator 的 code 写入和项目外路径由路径策略拒绝。

## 测试结果

- 新增 `tests/test_execution_broker.py`，覆盖 Backend 解耦、Tool Call 完成与重放、逻辑身份、terminal Event 去重和路径权限。
- F11.1 相关回归：23 passed。
- 全量回归：415 passed、104 subtests passed。

## 已知限制

- 本轮只定义 Contract 和 Broker，不实现 Docker、正式 Local Environment、Snapshot/Restore、Network 或 Credential 能力。
- 历史 `experimental/f11_execution_environment/` 仍是未晋级原型，不作为生产 Broker 后端。
- `project.yaml` 仍只能由 F10 CAS/commit-step 修改；Broker 不提供绕过 CAS 的写入口。

## 下一阶段入口

- F11.2 接入一个不依赖 Runtime 的实际 ExecutionEnvironment Backend，并补齐端到端 hash、超时和副作用验证。
