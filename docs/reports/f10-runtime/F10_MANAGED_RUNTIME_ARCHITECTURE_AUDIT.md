# F10 Managed Runtime 前置架构审计

## 1. 审计信息

- 审计日期：2026-07-30
- 审计分支：`codex/f8-5-skill-maintenance`
- 审计对象：`ai-development-team-skill` 工作副本
- 审计范围：`SKILL.md`、`AGENTS.md`、`config/`、`prompts/`、`scripts/`、`templates/`、`docs/`、`tests/`
- 工作区初始状态：干净
- 仓库根目录 `project.yaml`：不存在。该仓库是 Skill 本体，不是位于 `ai-projects/<project_id>` 的业务项目；本次没有创建替代状态文件。
- 工作副本与安装副本：各 113 个受比较文件，SHA-256 差异为 0；本次审计前完全一致。
- 限制：本阶段只审计和规划，不修改生产代码、现有 Schema 或安装副本。

## 2. 当前架构图

```text
用户 / Codex 会话
    |
    | 主动读取 project.yaml，解释 Prompt 和配置
    v
文件驱动工作流协议
    +-- config/workflow.yaml：状态、路由、等待点
    +-- config/role_policies.yaml：角色读写声明
    +-- prompts/：Planner / Generator / Evaluator 行为
    |
    v
确定性 Python 业务模块
    +-- project_state.py：安全 YAML、Schema/语义校验、原子状态写入
    +-- approval.py / exploration.py / feedback.py
    +-- evaluation_*.py：Gate、证据、Issue、受控返工和局部事务
    +-- change_request.py：追加式变更生命周期
    +-- project_migration.py：v3-v6 检查、预览、迁移、验证、回滚
    +-- skill_maintenance.py / f9_release_control.py
    |
    v
项目文件系统
    +-- project.yaml：业务当前状态的唯一权威来源
    +-- memory/、artifacts/、evaluation/、change_requests/、releases/
    +-- code/、tests/

缺失的运行时外围：
    Session Store -> Orchestrator -> Stateless Role Harness
    -> Execution Environment / Hands
```

## 3. 当前业务工作流层

现有业务治理边界完整且应保留：

1. 核心角色只有 Planner、Generator、Evaluator。
2. First-Ask 和 Change Request 是 Module，不是 Agent。
3. First-Ask 收集需求事实；需求充分后才进入 Planner。
4. Planner 先产出产品方案，必要时进行三方向 Design Exploration。
5. 产品方案和开发 Plan 分别要求用户明确批准。
6. Generator 只能执行获批来源链，且不得改变验收规则。
7. Evaluator 独立执行 Gate、生成证据和结构化 Issue。
8. 可返工 FAIL 才递增迭代；最多五次，之后等待用户。
9. 已完成项目通过 Change Request、Release Gate 和追加历史继续演进。

`config/workflow.yaml` 记录状态映射与等待态，但它本身不会调度。真正的“下一步由谁执行”仍由当前 Codex 会话读取 `active_module`、`status` 和 `next_role` 后解释决定。

## 4. 当前运行时层

当前没有独立 Managed Runtime。运行时能力分散在业务模块中：

- `scripts/project_state.py` 提供安全 YAML 子集、Schema/语义校验和原子替换。
- `scripts/evaluation_protocol.py` 为 Evaluation 提供 `STAGING`、`ARTIFACTS_COMMITTED`、`RECOVERY_REQUIRED`、`COMMITTED` 局部事务日志。
- `scripts/evaluation_evidence.py` 以参数数组、`shell=False`、项目内 cwd、白名单、超时、输出上限和有限环境变量执行验收命令。
- `scripts/change_request.py` 使用追加文件记录变更生命周期。
- `scripts/skill_maintenance.py` 为安装副本同步提供快照、校验和回滚。

这些能力没有统一 Session ID、事件序列、Checkpoint、工具调用记录、Worker 身份或跨进程恢复协议。进程重启后，只能从业务文件和少量局部 journal 人工推断进度。

## 5. 当前状态持久化方式

### 5.1 `project.yaml` 当前承担的职责

它同时承担：

- 当前业务状态与下一角色路由；
- 需求、方案、设计、规格、Plan、批准记录的活动指针；
- Evaluation、返工、升级和 Change Request 当前指针；
- Release、Skill Maintenance 目标和同步状态；
- Schema 版本与迁移记录。

它不应继续承担完整 Session 历史、每次角色运行、工具调用和恢复进度。

### 5.2 原子写入

`write_project_state_atomic` 会：

1. 校验结构和语义；
2. 在目标目录创建临时文件；
3. `flush` 和 `fsync` 文件；
4. 使用 `os.replace` 原子替换。

优点是单次写入不会留下半个 YAML。缺口是：

- 没有 `expected_revision`；
- 读取与替换之间没有 Compare-And-Swap；
- 没有目录 `fsync` 的明确耐久性协议；
- 不校验提交者是否持有 Lease；
- 两个 Worker 基于同一旧状态写入时，后写者可静默覆盖先写者。

### 5.3 追加式工件

多数历史工件采用“目标存在则拒绝”的追加策略，但编号常通过“扫描现有文件数量/最大编号 + 1”生成。并发调用可能选择同一编号；部分 helper 会二次检查冲突，却没有统一事务、幂等键或跨进程序列分配。

## 6. 当前故障模型

已覆盖：

- 无效 Schema 或业务状态拒绝写入；
- `project.yaml` 单文件写入中断；
- Evaluation 工件已提交但状态提交失败时保留 `RECOVERY_REQUIRED`；
- Skill 安装同步失败后从快照回滚；
- Change Request 状态与事件链不一致时给出只读恢复建议；
- v3-v6 迁移前备份与显式回滚。

未覆盖或覆盖不完整：

- 角色开始后、完成前进程崩溃；
- 工具已产生外部副作用但记录未落盘；
- 普通工件已写入而 `project.yaml` 尚未更新；
- `project.yaml` 已更新而统一事件尚未记录；
- 多 Worker 同时追加编号或提交状态；
- 旧 Worker 复活后覆盖新 Worker；
- Session 数据库与项目目录单边缺失；
- Evaluation 之外的跨工件事务恢复；
- 恢复命令重复执行时的全局幂等保证。

## 7. 当前权限模型

### 已有能力

- `config/role_policies.yaml` 声明模块/角色的读、写、禁止路径和字段所有权。
- Generator 来源链、Evaluator PASS 硬条件、最多重试次数均有确定性 Python 校验。
- 多处路径 helper 会拒绝绝对路径、`..` 和解析后逃逸。
- Evaluation 命令使用参数数组、白名单、`shell=False`、项目内 cwd、超时和输出限制。
- 安装副本有单独的只读直至最终同步边界。

### 关键缺口

- 大部分角色路径权限仍是协议声明，没有统一文件访问代理强制执行。
- Planner、Generator、Evaluator 仍可由宿主 Codex 工具能力直接访问文件；缺少按角色发放的运行时 capability。
- 不同脚本各自实现路径检查，规则可能漂移。
- F10 以前不应假装已经解决 F11/F12 的工具隔离和凭据边界。

## 8. 当前并发风险

1. `project.yaml` 无 revision/CAS，存在 lost update。
2. 无 Session 主 Worker Lease，同一 Session 可被多个进程同时推进。
3. 追加编号依赖目录扫描，存在 TOCTOU（检查后、使用前状态变化）竞争。
4. Change Request 事件序列由现有事件数量推导，没有数据库唯一事务分配。
5. 多文件提交顺序只在 Evaluation 局部实现，其他模块可能产生“工件已写、状态未写”的分裂。
6. 安装副本同步虽有基线和回滚，但没有 Worker Lease 与 Session 事件关联。
7. 原子替换只保证单文件可见性，不保证业务工件、状态和运行日志的原子一致性。

## 9. 当前安全风险

1. Generator 执行边界主要靠 Prompt、路径政策和部分 helper，不是统一强制代理。
2. `run_verified_command` 对 Evaluation 做了环境变量白名单，但其他宿主工具或未来 Harness 调用没有同一策略。
3. 当前敏感信息脱敏依赖正则，不能证明所有 Token、连接器凭据或派生 Secret 都被覆盖。
4. 没有统一 Tool Call 记录，无法审计某个角色何时、以何参数访问了外部系统。
5. 没有事件 Payload 大小、字段 Schema 和敏感字段禁止策略的统一入口。
6. 路径授权函数分散，符号链接/联接点和检查后替换风险需要在 F11 执行环境层继续加固。
7. 凭据代理、默认断网、短期授权属于 F12；不得塞入 F10 造成范围失控。

## 10. 当前可观测性缺口

现有工件能回答“业务结果是什么”，但不能稳定回答：

- 哪个 Session、Worker、角色运行产生了结果；
- 角色何时开始、结束或崩溃；
- 工具请求、开始、完成、超时之间发生了什么；
- 一个状态变更由哪个事件和用户批准触发；
- 当前恢复点和开放事务是什么；
- 是否发生并发冲突、Lease 过期或幂等重放；
- 跨 Session 的运行时指标、失败分类和耗时。

缺少统一的事件序列、关联 ID、因果 ID、Payload Hash、Checkpoint 和 inspect CLI。

## 11. 与目标 Managed Runtime 的差距

| 目标能力 | 当前状态 | F10 结论 |
|---|---|---|
| Session Store | 不存在 | 新增 SQLite 持久层 |
| 追加式统一 Event | 仅 Change Request 和 Evaluation 局部记录 | 新增不可更新事件表和确定性序列 |
| Stateless Role Harness | 依赖当前 Codex 会话上下文 | F10 只定义最小调用边界，不实现复杂 Context Builder |
| Deterministic Orchestrator | 配置存在，执行依赖会话解释 | 新增只做定位、校验、选角、记录、Checkpoint、恢复的程序 |
| revision/CAS | 只有原子替换 | 为业务状态投影增加 revision 和冲突事件 |
| Worker Lease | 不存在 | SQLite Lease，禁止无租约提交 |
| Checkpoint | 不存在 | 保存结构化恢复事实 |
| Execution Environment | Evaluation 有局部安全命令执行 | F11 实现统一接口，F10 不扩张 |
| Credential Boundary | 不存在统一代理 | F12 实现，F10 只保证事件不接收明显 Secret |
| Context Builder | 不存在 | F13 实现 |

## 12. F10 建议实施范围

F10 只建立可验证的耐久运行时骨架：

1. SQLite Session Store：`sessions`、`events`、`checkpoints`、`leases`、`tool_calls`、`state_revisions`。
2. 明确的 runtime schema version、数据模型、错误分类和事务边界。
3. 追加式 Event，数据库唯一约束保证 `(session_id, sequence)` 和幂等键。
4. `project.yaml.runtime` 最少投影字段；旧项目首次读取不写回。
5. revision/CAS：读取、写前复核、冲突拒绝、冲突事件、重新读取。
6. Worker Lease：acquire、renew、release、detect expired、steal expired。
7. 确定性 Orchestrator：只根据现有状态选择三角色或等待，不作产品/代码/验收决策。
8. Checkpoint 与针对列明崩溃窗口的幂等恢复。
9. CLI：start、resume、inspect、pause、recover。
10. v3-v6 到 v7 的检查、预览、迁移、验证和回滚，不静默迁移。
11. 用户要求列出的全部新测试，并运行现有 341 项完整回归。

## 13. 明确不属于 F10

- Docker 或其他容器执行环境；
- 通用 `ExecutionEnvironment` 和 Generator Shell 代理；
- Credential Vault、Token Broker、MCP/GitHub 受控代理；
- 默认断网和域名级网络策略；
- 多 Agent 并行、同角色多 Worker、Task Graph；
- 复杂 Context Builder、压缩和长期摘要；
- 重写 Design Exploration、Evaluation 或 Change Request；
- 增加第四个 Agent；
- 自动同步安装副本；
- 自动进入 F11、F12 或 F13。

## 14. 兼容性风险

1. v7 `runtime` 字段不能直接加入 v3-v6 必填项，否则旧项目首次读取会失效。
2. CAS 接入所有状态写入点工作量较大；遗漏任何 writer 都会绕过 revision。
3. SQLite 与 `project.yaml` 是两个耐久介质，提交顺序和崩溃补偿必须写成协议并以失败注入测试证明。
4. Windows 文件替换、目录同步和 SQLite locking 行为与 POSIX 不完全一致。
5. 现有测试大量使用 `tempfile`；受限环境必须提供可写临时目录，否则会产生环境假失败。
6. 现有追加编号与新 Event sequence 不能混为一谈；业务工件命名仍需兼容历史。
7. 仓库根目录没有业务 `project.yaml` 是预期布局，但 Runtime CLI 必须明确要求一个真实项目根目录，不能把 Skill 本体误当业务项目。

## 15. 回滚方案

F10 应采用可逆外围接入：

1. 新 Runtime 默认对旧项目执行只读 inspect/preview，不自动写入。
2. 迁移前保存 `project.yaml` 备份，并创建追加式 migration record。
3. Session SQLite 独立于业务工件；禁用 Runtime 时，v3-v6 原工作流仍可按旧路径读取。
4. v7 回滚先暂停 Session、确认无有效 Lease，再保存当前 v7 状态，恢复原 Schema 项目文件。
5. 数据库迁移只前向追加，不原地删除表或列；必要时保留数据库供审计。
6. 若 `project.yaml` 已提交但 Event 未提交，recover 根据 state revision/hash 补写唯一事件；反向窗口则不重复提交状态。
7. F10 分支在完整回归和迁移回滚测试通过前不得合并或同步安装副本。

## 16. 测试基线

### 16.1 命令 1：默认 Python

```powershell
python -m unittest discover -s tests -p test_*.py
```

- 退出码：1
- 结果：PowerShell 找不到 `python` 命令。

### 16.2 命令 2：Bundled Python、只读沙箱

```powershell
& 'C:\Users\28388\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

- 退出码：1
- 运行：341 项
- 错误：164
- 根因：只读沙箱中没有可用临时目录，`tempfile.TemporaryDirectory` 抛出 `FileNotFoundError`。
- 结论：环境失败，不是产品代码断言失败。

### 16.3 命令 3：Bundled Python、允许系统临时目录

```powershell
& 'C:\Users\28388\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

- 退出码：0
- 运行：341 项
- 通过：341
- 失败：0
- 错误：0
- 跳过：0
- 耗时：13.685 秒

## 17. 主要阻塞项与决策点

- 技术阻塞：无，现有完整基线通过。
- 环境约束：PATH 中没有 Python；测试必须使用 Bundled Python 绝对路径或明确配置运行时。
- 实施前决策：用户必须明确批准 `F10_DURABLE_SESSION_RUNTIME_PLAN.md` 的范围、v7 迁移策略、SQLite 双介质提交顺序和 Lease/CAS 规则。
- 当前不应实施：尚未获得 F10 实施批准。
