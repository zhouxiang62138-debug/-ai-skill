---
name: ai-development-team-skill
description: 使用 First-Ask Intake Module 与文件驱动的 Planner、Generator、Evaluator 协作协议，收集需求、规划、设计探索、开发、验收并迭代隔离的软件应用项目。适用于初始化项目、澄清新 App 需求、推进项目状态、处理验收返工，或审计 AI 开发团队的项目状态。
---

# AI Development Team Skill

使用本 Skill 管理位于 `C:\Users\28388\Desktop\ai-projects\<project_id>` 的独立软件项目。Skill 本体只提供规则、Prompt 和模板；不得保存任何具体项目的数据或代码。

## Skill 本体仓库与 managed project 边界

本仓库是 Skill 本体，不是 managed project。仓库根目录不要求存在
`project.yaml`，不得为了满足项目协议创建假的 `project.yaml`。
`project.yaml` 只属于 `C:\Users\28388\Desktop\ai-projects\<project_id>`
下的项目实例。

Skill 本体开发、测试、文档维护、Git commit、Git push 和 GitHub 上传不要求
`project.yaml`，不得因 Skill 仓库根目录缺少该文件而 BLOCKED。只有 Planner、
Generator、Evaluator、Change Request 等项目工作流，才按本节协议读取和更新
对应 managed project 的 `project.yaml`。

## 开始 managed project 工作

1. 定位唯一目标项目；未经用户明确授权，不读取其他项目。
2. 读取项目根目录唯一的 `project.yaml`，它是项目状态的唯一可信来源。
3. 如果 `active_module: first_ask_intake`，读取 `intake/first_ask.md`；否则读取 `next_role` 对应的角色 Prompt。验证当前状态的必需输入。
4. 按 `config/workflow.yaml` 和 `config/role_policies.yaml` 执行角色任务。
5. 创建不可覆盖的计划、交接、报告或验证工件。
6. 更新项目根目录的 `project.yaml`，决定继续、返工、等待用户、阻塞或结束。

这是文件驱动协议，不是自行执行的控制程序。`project.yaml` 的 `current_iteration` 达到 5 后，必须进入 `WAITING_FOR_USER`，不得自动继续修改。

## F10 持久化运行时

schema v7 项目在原文件驱动业务协议外围增加外部 Session Control Plane；运行历史位于
`~/.ai-development-team/runtime/<control_plane_id>/sessions.sqlite3`，不得写入项目目录。
Runtime 通过确定性 Orchestrator 管理 Session、追加 Event、Worker Lease、
revision/CAS、Checkpoint 和崩溃恢复；它不是 Agent，也不能做产品决策、写业务
代码或替代 Evaluator 判定结果。`project.yaml` 仍是业务当前状态的权威投影，
完整运行历史不写入 YAML。

旧 schema v3-v6 首次读取保持只读。使用
`scripts/project_migration.py runtime-preview/runtime-migrate/runtime-verify`
显式预览、备份、迁移和验证；回滚前保留 v7 状态。v7 状态只能由持有有效
Worker Lease 的 Runtime CAS 提交。Runtime CLI 见 `python -m runtime.cli --help`，
完整协议见 `docs/MANAGED_RUNTIME_ARCHITECTURE.md`。

F11–F13 的正式实现已经进入 `runtime/` 正式 Runtime 路径：F11 提供
ExecutionBroker、LocalCompatibilityEnvironment 与 Snapshot / Restore，F12 提供
Capability、Credential、Network / External Tool Security，F13 提供 Deterministic
Context Builder、Context Budget、Incremental Resume 与 Rollover/Fresh Invocation。
Evaluator 还可追加 Candidate 验证记录，Runtime 通过 Snapshot Service 管理恢复。
`experimental/` 下同名目录只
保留为历史原型，不是正式调用路径。Docker Sandbox 仍为 `DEFERRED`，且
`LocalCompatibilityEnvironment` 不等于物理 Sandbox。完整当前状态见
`docs/RUNTIME_CAPABILITY_STATUS.md`。这些能力都不得绕过产品/Plan 批准链。

## E1 Evaluator Independence Hardening

E1 是独立的“验收可信度”升级，与 F11 Execution、F12 Security、F13 Context
并列，不属于其中任何一个阶段。它不增加第四个 Agent；Planner、Generator、Evaluator
仍是唯一三个核心 Agent。

用户仍然只需要一个 Codex 窗口，但 Runtime 必须为 Planner、Generator 和 Evaluator
创建独立的 Model Invocation。Evaluator 的 Invocation 必须是 Fresh Invocation，使用
`EVALUATOR_INDEPENDENT` Context Manifest，不继承 Generator 完整聊天/推理历史，也不
把 Generator 自我声明当作 PASS Evidence。

E1 的最终 PASS 必须同时满足独立重现、Evidence Provenance、当前 project revision /
code snapshot 绑定、受保护工件完整性、无 blocking/critical Issue 和 Runtime
Deterministic PASS Gate。模型只能提出 PASS，Python/Runtime 决定是否允许提交。
正式策略见 `config/evaluation_independence.yaml`，实现见
`runtime/evaluator_independence.py`；宿主无法暴露外层对话隔离能力时，只能报告
`Runtime Context Isolation`，不得伪造 `Host Conversation Isolation`。

### 运行时故障与用户打断边界

- 只有需求事实、设计选择、产品方案确认、Plan 批准、不可逆操作授权或确实需要
  用户补充的外部凭据，才允许暂停并要求用户处理。
- 路径导入、候选字段校验、Lease、CAS、PENDING/ABORTED revision、浏览器临时视口
  和可安全重试的工具失败属于 Runtime 内部责任。先记录诊断并自动恢复或换用正式
  入口，不得把数据库清理、字段补齐或接口选择交给项目用户。
- 候选状态必须在创建 revision 尝试前完成确定性预检。ABORTED 尝试必须保留审计，
  但不得占住业务 revision 或要求删除数据库记录后才能继续。
- 生命周期字段 `status`、`next_role` 与 `active_module` 由 Runtime 按目标路由派生；
  Planner、Generator、Evaluator 只提交各自拥有的业务字段。
- 同一内部步骤最多自动修正 2 次；仍失败时只报告一个聚合后的根因、已保留的工件
  和最小必要用户动作，不逐条播报中间异常。

## 角色选择

- `first_ask_intake`：读取 `intake/first_ask.md`；它是 Planner 前置模块，不是 Agent，只收集事实、目标和约束并写入当前项目 `memory/requirements/`。
- `planner`：读取 `prompts/planner_prompt.md`；只读取 Intake 需求，创建产品方案、决策、计划、交接和状态更新，不修改需求快照或代码。
- `generator`：读取 `prompts/generator_prompt.md`；只在当前项目 `code/` 和允许的证据目录实现、测试并交接，不修改评估规则。
- `evaluator`：读取 `prompts/evaluator_prompt.md` 和选定的评估 Profile；只验收、出报告和更新状态，不修改代码或计划。

## 资源

### 需求入口

新项目先由 First-Ask Intake Module 保存用户原始请求，按轮次提出 1～3 个高价值问题，并创建追加式结构化需求快照。`requirements_status: sufficient_for_planning` 且 `active_requirements` 有效后，才能进入 Planner。视觉类 `undecided` 不继续采访，交给 Design Exploration。模板见 `templates/original_request.md`、`templates/requirements_interview.md` 和 `templates/requirements_snapshot.yaml`。

### 产品确认流程

Planner 在任何开发计划前都必须先产出可审核的产品方案。用户明确确认产品方案
后，Planner 生成正式产品规格和待审核开发 Plan；只有用户再次独立批准 Plan，
才能交给 Generator。详细状态机、文件约定和交互示例见
`docs/PLANNER_APPROVAL_WORKFLOW.md`；产品方案使用
`templates/product_proposal.md`。

### Design Exploration

当用户尚未确定 App 的视觉风格、页面布局，或明确希望先看参考方案时，
Planner 默认先生成一轮 3 个轻量产品方向：每个方向只写 `concept.md`，三者
共用 `comparison.html` 与 `comparison.css`。三套路线必须在定位、特色功能、
主要用户路径或信息架构上存在实质差异，不能只换颜色。

用户单选、融合、修改或恢复方向后，Planner 开启新轮次，只为选中结果生成一套
`selected_concept/concept.md`、`preview.html` 与 `preview.css`，完整浏览器验证
也只执行这一套。用户明确确认该高保真预览后，Planner 才将其整合进新的产品
方案版本并再次等待产品确认；方向选择、高保真确认、产品确认和 Plan 确认是四个
不同门禁。

第一阶段方向说明使用 `templates/design_direction.md`；选中方向后的完整原型说明
使用 `templates/design_concept.md`。每次用户反馈使用
`templates/design_feedback.md`，明确选择使用
`templates/design_selection.md`，跳过设计探索时使用
`templates/design_skip_decision.md`。预览仅写入项目的
`artifacts/design_previews/`，不得写入 Skill 目录或项目 `code/`。完整规范
见 `docs/DESIGN_EXPLORATION_WORKFLOW.md` 和 `docs/PLANNER_APPROVAL_WORKFLOW.md`。

- 状态协议与迁移规则：读取 `docs/workflow_protocol.md`。
- 项目目录、命名和隔离规则：读取 `docs/project_conventions.md`。
- 新建工件时：从 `templates/` 选择对应模板。
- 评估时：从 `config/evaluation_rules/` 读取 `project.yaml` 中 `evaluation_profile` 对应的 Profile。

### F9 结构化验收闭环

Evaluator 同时生成 Markdown 报告、`evaluation/issues/` Issue Package 和
`evaluation/evidence/` Manifest；Generator 在 `memory/handoffs/responses/`
逐项回应。确定性规则分别位于 `config/evaluation_protocol.yaml`、
`config/evaluation_gates.yaml` 和 `config/retry_governance.yaml`，实现位于
`scripts/evaluation_protocol.py`、`scripts/evaluation_evidence.py` 和
`scripts/evaluation_governance.py`。

必需 Gate 或证据缺失、开放 blocker/critical、受保护工件未授权修改时不得 PASS。
只有发往 Generator 的可返工 FAIL 增加 `current_iteration`；提前升级或第 5 次
失败后停止自动返工。新项目使用 project schema v7；旧项目迁移使用
`scripts/project_migration.py` 的检查、预览、迁移、验证和回滚动作。

### 已完成项目 Change Request

用户提供已有项目路径和新的修改意见时，读取该项目根目录唯一的
`project.yaml`。若状态为 `ACCEPTED` 或 `ARCHIVED`，使用
`scripts/change_request.py` 与 `config/change_request.yaml` 创建追加式
Change Request；不得新建同名项目、重新执行 First-Ask 或要求用户重述全部历史。

Change Request 是 Module，不是 Agent。Planner 先生成影响分析，明确批准前禁止
改代码；部分批准只实施获批项。Generator 开始前必须建立稳定基线并通过范围
Gate；Evaluator 必须逐项验收并执行原功能回归。PASS 后先进入
`RELEASE_READY`，只有 Release 成功写入才回到 `ACCEPTED`。

同一项目只允许一个 `active_change_request`。返工上限按当前请求的
`change_context.evaluation_iteration` 计算，新请求从 0 开始。完整协议、迁移、
回滚、用户示例和测试方式见
`docs/COMPLETED_PROJECT_CHANGE_REQUEST_WORKFLOW.md`。

### Role Thread Isolation

Planner、Generator、Evaluator 仍是唯一三个核心 Agent；Main Codex Thread 是用户
交互与 Control Surface，不是第四个 Agent。每次正式 Role Run 由
`runtime/role_execution.py` 创建独立的 Role Execution，并持久化
`role_execution_id`、`execution_mode`、`host_thread_id`、`invocation_id`、Context
Manifest、Workspace Binding 和 source revision。

Runtime 优先请求真实 Child Thread。只有宿主明确提供 Child Thread、稳定线程 ID
和程序化创建能力时，才记录 `CHILD_THREAD`；否则明确记录
`ROLE_EXECUTION_FALLBACK` 和 `HOST_CHILD_THREAD_UNAVAILABLE`，使用新的
`FRESH_INVOCATION`，且不伪造 `host_thread_id`。每个 Role Run 不复用已完成线程。
Role Context 仍只能由 Deterministic Context Builder 构建；Role Execution 不会
绕过 Role Policy、Lease、Attestation 或 CAS。
