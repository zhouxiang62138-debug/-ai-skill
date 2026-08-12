# F10 实施报告

## 结果

F10 Durable Session Runtime 已在 `codex/f10-durable-session-runtime` 实现并自审通过。
它在不改变 Planner、Generator、Evaluator 业务职责的前提下，新增了 SQLite Session
Store、不可变 Event、Worker Lease、Checkpoint、v7 Runtime 投影、CAS、确定性
Orchestrator、CLI 和幂等恢复。

## 新增

- `runtime/`：Session Store、Event 类型、数据模型、Lease、CAS、Checkpoint、角色
  选择、Orchestrator、Recovery、CLI。
- `config/runtime.yaml` 与 8 个 runtime/v7 Schema。
- 四份 Runtime 协议文档。
- 22 个 F10 测试文件/用例覆盖 Session、事件、Lease、CAS、迁移、恢复、Orchestrator
  和安全 Payload。

## 修改

- `scripts/project_state.py`：支持 v7，并将 v7 直接写入锁定为 Runtime 授权路径。
- `scripts/project_migration.py`：保留旧 v6 迁移，新增显式 v7 preview/migrate/verify；
  迁移初始化独立 SQLite Session Store。
- `scripts/evaluation_protocol.py`：将候选状态暂存进事务目录，支持幂等恢复
  `RECOVERY_REQUIRED`。
- 工作流、角色策略、模板、技能说明和项目协议均增加 v7 Runtime 规则。

## 删除

无。历史工件、旧 Schema、旧迁移入口和现有业务流程均保留。

## 关键机制

- Session/Event：SQLite 事务内分配 sequence；Event 禁止 UPDATE/DELETE，幂等键唯一。
- revision：CAS 比较 `expected_revision` 与 state hash；冲突写入 Event，拒绝覆盖。
- Lease：Worker/lease_version 双栅栏；旧 Worker 不能续约、释放或提交新 Lease。
- 恢复：pending revision 依据 YAML 的 revision/hash 变为 COMMITTED 或 ABORTED；
  已完成 Tool Call 只重用结果引用；Evaluation 使用暂存状态重放。
- Orchestrator：只选现有三个角色或 WAIT，不做产品决策、代码编写或 PASS/FAIL。

## F11 明确未实现

统一 `ExecutionEnvironment`、Docker、Generator 通用 Shell 代理、网络隔离和可销毁
Sandbox 属于 F11，未在 F10 实现。
