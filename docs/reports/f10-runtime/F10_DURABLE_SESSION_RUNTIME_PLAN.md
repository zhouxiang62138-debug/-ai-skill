# F10 Durable Session Runtime 正式实施计划

## 1. 计划状态

- 状态：`WAITING_FOR_USER_APPROVAL`
- 前置审计：`F10_MANAGED_RUNTIME_ARCHITECTURE_AUDIT.md`
- 目标：在不削弱现有业务治理的前提下，增加持久化 Session、统一事件日志、revision/CAS、Worker Lease、Checkpoint 和可恢复的确定性 Orchestrator。
- 授权边界：本文件获明确批准后才建立 F10 分支并实施；批准 F10 不等于批准 F11-F13。

## 2. 不变量

1. 核心角色仍只有 Planner、Generator、Evaluator。
2. Orchestrator、Session Manager、Recovery、First-Ask、Change Request 都不是 Agent。
3. `project.yaml` 仍是业务当前状态的权威投影。
4. SQLite 保存运行历史，不取代需求、方案、计划、验收和 Release 工件。
5. 产品批准、Plan 批准、Generator Gate、Evaluator PASS 硬条件、最大五次返工和 Release Gate保持不变。
6. 安全与并发规则由 Python 和确定性验证控制，不依赖 Prompt。
7. v3-v6 项目第一次读取不得被修改。
8. 历史事件、工件、迁移和批准记录不得覆盖或删除。

## 3. 预计文件变更

### 3.1 新增 Runtime 模块

```text
runtime/
  __init__.py
  models.py
  errors.py
  event_types.py
  session_store.py
  leases.py
  project_revision.py
  checkpoints.py
  role_selector.py
  orchestrator.py
  recovery.py
  cli.py
```

- `models.py`：Session、Event、Checkpoint、Lease、ToolCall、StateRevision dataclass。
- `errors.py`：Validation、Conflict、Lease、Recovery、Storage、ProjectMissing 等分类错误。
- `event_types.py`：封闭事件枚举、actor 枚举、Payload 大小上限。
- `session_store.py`：SQLite 初始化、事务、追加事件、查询和不可变性保护。
- `leases.py`：租约获取、续约、释放、过期检测和过期接管。
- `project_revision.py`：v7 runtime 投影、状态 hash、CAS 提交适配器。
- `checkpoints.py`：结构化 Checkpoint 创建和一致性验证。
- `role_selector.py`：根据 `active_module`、`status`、`next_role` 选择现有角色或等待。
- `orchestrator.py`：确定性执行顺序与 Harness 调用接口，不包含角色业务决策。
- `recovery.py`：崩溃窗口分类、幂等恢复动作和冲突升级。
- `cli.py`：`start`、`resume`、`inspect`、`pause`、`recover`。

### 3.2 新增配置与 Schema

```text
config/runtime.yaml
config/schemas/project_v7.schema.json
config/schemas/runtime_session_v1.schema.json
config/schemas/runtime_event_v1.schema.json
config/schemas/runtime_checkpoint_v1.schema.json
config/schemas/runtime_lease_v1.schema.json
config/schemas/runtime_tool_call_v1.schema.json
config/schemas/runtime_state_revision_v1.schema.json
config/schemas/runtime_migration_record_v1.schema.json
```

说明：JSON Schema 用于外部工件/投影验证；SQLite 表还要通过 DDL 约束、事务和 Python 模型做等价验证。

### 3.3 修改现有实现

```text
scripts/project_state.py
scripts/project_migration.py
scripts/evaluation_protocol.py
scripts/evaluation_evidence.py
scripts/change_request.py
scripts/skill_maintenance.py
config/workflow.yaml
config/role_policies.yaml
templates/project.yaml
SKILL.md
AGENTS.md
docs/workflow_protocol.md
docs/project_conventions.md
docs/project_state_schema.md
```

修改原则：

- `project_state.py` 保留现有原子替换，增加 v7 校验和受 Lease 约束的 CAS writer；旧 writer 仅用于明确兼容/迁移路径。
- 所有生产状态写入点逐一接入同一 CAS adapter，禁止局部自造 revision。
- Evaluation 局部 journal 与 Runtime Event 通过关联 ID 接入，不重写现有 PASS/Gate 逻辑。
- Change Request 业务事件继续保留；Runtime Event 记录运行因果，两者不互相替代。
- Skill 安装同步只记录 Session 关联；F10 不改变同步业务算法。

### 3.4 新增协议文档

```text
docs/MANAGED_RUNTIME_ARCHITECTURE.md
docs/SESSION_EVENT_PROTOCOL.md
docs/ORCHESTRATOR_PROTOCOL.md
docs/RECOVERY_PROTOCOL.md
```

### 3.5 F10 完成报告

```text
F10_MANAGED_RUNTIME_IMPLEMENTATION_REPORT.md
F10_MANAGED_RUNTIME_TEST_REPORT.md
F10_MANAGED_RUNTIME_MIGRATION_REPORT.md
F10_MANAGED_RUNTIME_SECURITY_REVIEW.md
```

## 4. SQLite 数据设计

数据库建议位于项目 Runtime 数据目录，由 CLI 显式解析；路径不得写死 Windows 用户目录。启用：

- foreign keys；
- WAL 或经 Windows 实测后选择的 journal mode；
- busy timeout；
- 显式事务；
- schema version 与 migration table。

最低表：

### `sessions`

主键 `session_id`；字段含 `project_id`、规范化项目根引用、状态、创建/更新时间、最后事件序列、最后 Checkpoint、活动 Worker。

### `events`

- 主键 `event_id`；
- 唯一 `(session_id, sequence)`；
- 唯一 `(session_id, idempotency_key)`；
- 外键 `session_id`；
- Payload 只允许 JSON，写前 canonicalize 并计算 SHA-256；
- 禁止 UPDATE/DELETE，可通过 SQLite trigger 和只暴露 append API 双重保护。

### `checkpoints`

保存 event sequence、project revision/hash、active role/module、status、next role、开放事务 ID；Checkpoint 不保存自然语言摘要替代事实。

### `leases`

每个 Session 最多一个当前 Lease；使用 `lease_version` 防旧 Worker 续约或释放新 Lease。

### `tool_calls`

保存请求、状态、超时、结果引用和 Payload hash；F10 不执行通用工具隔离，只建立可恢复记录。

### `state_revisions`

保存 revision、前后 hash、提交状态、关联事件和幂等键，用于双介质恢复。

## 5. Event 协议

实现需求文本列出的全部事件类型。额外约束：

1. `sequence` 在 SQLite 写事务中分配，不以目录扫描结果推导。
2. `event_id` 由确定性规则基于 Session 和 sequence 生成。
3. 时间使用带时区 UTC。
4. `caused_by_event_id` 和 `correlation_id` 必须可校验。
5. `idempotency_key` 在 Session 内唯一。
6. Payload canonical JSON 后计算 hash，并限制字节数。
7. Event 不保存 Secret、完整凭据或未受限工具长输出；长输出只保存引用和 hash。
8. 旧业务工件保持原样，Event 引用它们而不是复制全部内容。

## 6. `project.yaml` v7 投影

最少新增：

```yaml
runtime:
  session_id: session-...
  revision: 0
  last_event_sequence: 0
  last_checkpoint_id: null
```

兼容规则：

- v3-v6 加载和 inspect 继续通过，且不写入 `runtime`。
- 新建 Runtime Session 时，必须先执行迁移检查；只有显式 migrate 才生成 v7。
- 完整 Event 不进入 YAML。
- `revision` 只在成功业务状态提交后递增。
- `last_event_sequence` 的具体提交顺序按第 7 节恢复协议处理，不能用“看起来一致”的双写代替可恢复事务。

## 7. CAS 与双介质提交顺序

建议固定顺序：

1. 验证有效 Lease 和 `lease_version`。
2. 读取当前 YAML revision/hash。
3. 在 SQLite 写入 `PROJECT_STATE_COMMIT_REQUESTED` 和 pending `state_revision`。
4. 写前重新读取 YAML 并比较 `expected_revision` 和 hash。
5. 不一致：原事务记录冲突，追加 `PROJECT_STATE_CONFLICT`，拒绝覆盖并重新读取。
6. 一致：以临时文件、文件 `fsync`、原子替换提交 `project.yaml`。
7. SQLite 将 revision 标记 committed，追加 `PROJECT_STATE_COMMITTED`。
8. 创建 Checkpoint。

崩溃恢复：

- 步骤 3 后、6 前：YAML revision 未变，pending 可安全取消或重试。
- 步骤 6 后、7 前：根据 YAML revision/hash 补写唯一 committed 记录与 Event。
- 重复 recover：由 state revision 唯一约束和幂等键返回同一结果。

目录 `fsync` 在 Windows 不可按 POSIX 假设实现；需要记录平台能力和降级保证。

## 8. Worker Lease 规则

- acquire 仅在无 Lease 或 Lease 已过期时成功；
- renew 必须同时匹配 `session_id`、`worker_id`、`lease_version` 且未过期；
- release 同样校验版本，避免旧 Worker 释放新 Lease；
- steal_expired 在单个 SQLite 事务中检查过期并递增 `lease_version`；
- Orchestrator 每次状态提交前再次校验 Lease，不只在角色开始时校验；
- 旧 Worker 恢复时发现版本不匹配，立即停止写入并记录冲突；
- Lease 使用 UTC wall clock 存证，同时用数据库事务防竞态；明确处理系统时钟回拨风险。

## 9. Orchestrator 和 Harness 边界

Orchestrator 只允许：

1. 定位项目和 Session；
2. 获取/续约/释放 Lease；
3. 读取并校验 `project.yaml`；
4. 根据确定性映射选择 First-Ask Module、三个现有角色或 WAIT；
5. 创建最小角色运行请求；
6. 记录角色和工具生命周期 Event；
7. 提交 CAS、创建 Checkpoint、执行恢复。

禁止：

- 生成产品方案或代码；
- 判断产品方向；
- 替代 Evaluator 判定 PASS/FAIL；
- 修改用户需求；
- 越过等待用户和双重批准 Gate；
- 把 First-Ask、Orchestrator 或 Recovery 命名为 Agent。

F10 Harness 只定义“可重启调用协议和结果信封”，不实现 F13 Context Builder，也不引入多 Worker 并行。

## 10. 实施顺序

### 阶段 1：Runtime 数据模型与 Session Store

- 新增 errors、models、event types、SQLite DDL。
- 完成 Session/Event 最小测试。
- 验证事件不可修改、sequence 和幂等键。

### 阶段 2：Lease 与 Checkpoint

- 实现 Lease 事务和过期接管。
- 实现结构化 Checkpoint。
- 完成并发/过期/旧 Worker 测试。

### 阶段 3：v7 投影、迁移与 CAS

- 扩展安全 YAML validator 和迁移工具。
- 先完成 v3-v6 preview，再实现显式 migrate/verify/rollback。
- 接入 revision/hash 和状态冲突测试。

### 阶段 4：确定性 Orchestrator

- 实现角色选择和等待态。
- 接入 Lease、Event、Checkpoint。
- 使用假的 Harness adapter 验证控制流，不调用真实模型完成业务决策。

### 阶段 5：恢复

- 对每个要求的崩溃窗口做失败注入。
- 证明重复恢复不产生重复工件、事件或 revision。
- 接入现有 Evaluation `RECOVERY_REQUIRED`。

### 阶段 6：现有 writer 接入与文档

- 清点所有 `project.yaml` 写入调用点并接入 CAS。
- 更新协议文档，不改变业务 Gate。
- 运行定向测试和完整回归。

### 阶段 7：报告与停止

- 生成四份 F10 报告。
- 比较工作副本与安装副本，但不自动同步。
- 列出实际修改文件、命令、退出码、通过/失败/跳过。
- 停止并等待用户批准 F11。

## 11. 测试计划

必须新增用户指定的测试：

```text
tests/test_session_store.py
tests/test_event_append_only.py
tests/test_event_sequence.py
tests/test_event_idempotency.py
tests/test_session_recovery.py
tests/test_project_revision_cas.py
tests/test_worker_lease.py
tests/test_expired_lease_recovery.py
tests/test_orchestrator_role_selection.py
tests/test_orchestrator_wait_states.py
tests/test_checkpoint_creation.py
tests/test_crash_after_tool_completion.py
tests/test_crash_before_project_state_commit.py
tests/test_crash_after_project_state_commit.py
tests/test_recovery_is_idempotent.py
tests/test_legacy_project_without_runtime_fields.py
tests/test_schema_v3_to_v7_preview.py
tests/test_schema_v4_to_v7_preview.py
tests/test_schema_v5_to_v7_preview.py
tests/test_schema_v6_to_v7_preview.py
tests/test_migration_rollback.py
```

补充建议：

```text
tests/test_event_payload_limits.py
tests/test_event_payload_redaction.py
tests/test_lease_version_fencing.py
tests/test_old_worker_cannot_commit.py
tests/test_session_db_missing.py
tests/test_project_files_missing.py
tests/test_sqlite_schema_migration.py
tests/test_runtime_windows_paths.py
tests/test_all_project_state_writers_use_cas.py
```

验证层次：

1. 每新增一个模块立即运行对应最小测试。
2. 阶段完成后运行全部 Runtime/迁移测试。
3. 最后运行：

```powershell
& 'C:\Users\28388\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

验收条件：

- 现有 341 项不得减少；
- 失败 0、错误 0；
- 不通过 skip 制造成功；
- 新旧迁移、回滚、CAS、Lease 和失败注入全部可复现；
- 记录真实命令、退出码、通过/失败/跳过和环境限制。

## 12. 迁移步骤

1. `check`：识别 v3-v6 版本、状态、活动事务和人工复核要求。
2. `preview`：生成内存中的 v7 投影与差异，不写文件、不建 Session。
3. `backup`：要求用户指定不存在的备份目标。
4. `initialize-session-db`：创建 SQLite schema，但未提交 YAML 前 Session 标记为 pending migration。
5. `migrate`：使用 CAS/原子写入生成 v7 YAML，创建 migration record 和初始 Event。
6. `verify`：校验 YAML Schema、DB 外键/唯一约束、revision/hash、Session/Checkpoint 一致性。
7. 成功后 Session 才进入可 resume 状态。

处于实施、验收、`RECOVERY_REQUIRED`、活动 Change Request 或 Release 过程中的项目必须先给出明确风险并按规则阻塞自动迁移。

## 13. 回滚步骤

1. pause Session；
2. 验证没有有效 Worker Lease；
3. 保存当前 v7 YAML 和 SQLite 快照引用；
4. 使用迁移前备份恢复 v3-v6 `project.yaml`；
5. 验证旧 Schema 和业务语义；
6. 追加 migration rollback record 和 Runtime Event；
7. 将 Session 标记为 rolled back/paused，不删除数据库；
8. 旧文件驱动工作流可继续读取原 Schema。

若 Lease 无法安全收回、备份 hash 不匹配或项目状态与 DB revision 冲突，回滚进入 `BLOCKED`，不得强行覆盖。

## 14. 风险控制

- 双介质一致性：以 pending revision + hash + 幂等恢复解决，不宣称跨文件系统和 SQLite 的真正单事务。
- SQLite 并发：使用短事务、唯一约束、busy timeout 和 Lease fencing；不依赖普通锁文件。
- Writer 漏接：增加静态/行为测试，列出所有 `write_project_state_atomic` 调用点。
- Windows：专测文件替换、路径大小写、联接点、SQLite locking 和临时目录。
- Secret：Event 只接收验证后的有限 JSON；F10 不把凭据注入 Harness。
- 范围蔓延：Execution Environment、Vault、网络隔离、Context Builder 和并行均推迟。

## 15. 用户批准语句

下一步所需的准确批准内容是：

> 我批准按照 `F10_DURABLE_SESSION_RUNTIME_PLAN.md` 实施 F10 Durable Session Runtime；允许创建 F10 阶段分支，新增 Runtime/测试/文档和 project v7 Schema，修改列明的现有状态写入与迁移适配代码，运行完整测试并生成四份 F10 报告。不得进入 F11、F12 或 F13，也不得自动更新 Skill 安装副本。

没有这项明确批准，不开始实现。
