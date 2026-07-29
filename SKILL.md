---
name: ai-development-team-skill
description: 使用 First-Ask Intake Module 与文件驱动的 Planner、Generator、Evaluator 协作协议，收集需求、规划、设计探索、开发、验收并迭代隔离的软件应用项目。适用于初始化项目、澄清新 App 需求、推进项目状态、处理验收返工，或审计 AI 开发团队的项目状态。
---

# AI Development Team Skill

使用本 Skill 管理位于 `C:\Users\28388\Desktop\ai-projects\<project_id>` 的独立软件项目。Skill 本体只提供规则、Prompt 和模板；不得保存任何具体项目的数据或代码。

## 开始项目工作

1. 定位唯一目标项目；未经用户明确授权，不读取其他项目。
2. 读取项目根目录唯一的 `project.yaml`，它是项目状态的唯一可信来源。
3. 如果 `active_module: first_ask_intake`，读取 `intake/first_ask.md`；否则读取 `next_role` 对应的角色 Prompt。验证当前状态的必需输入。
4. 按 `config/workflow.yaml` 和 `config/role_policies.yaml` 执行角色任务。
5. 创建不可覆盖的计划、交接、报告或验证工件。
6. 更新项目根目录的 `project.yaml`，决定继续、返工、等待用户、阻塞或结束。

这是文件驱动协议，不是自行执行的控制程序。`project.yaml` 的 `current_iteration` 达到 5 后，必须进入 `WAITING_FOR_USER`，不得自动继续修改。

## 角色选择

- `first_ask_intake`：读取 `intake/first_ask.md`；它是 Planner 前置模块，不是 Agent，只收集事实、目标和约束并写入当前项目 `memory/requirements/`。
- `planner`：读取 `prompts/planner_prompt.md`；只读取 Intake 需求，创建产品方案、决策、计划、交接和状态更新，不修改需求快照或代码。
- `generator`：读取 `prompts/generator_prompt.md`；只在当前项目 `code/` 和允许的证据目录实现、测试并交接，不修改评估规则。
- `evaluator`：读取 `prompts/evaluator_prompt.md` 和选定的评估 Profile；只验收、出报告和更新状态，不修改代码或计划。

## 资源

### 需求入口

新项目先由 First-Ask Intake Module 保存用户原始请求，按轮次提出 1～3 个高价值问题，并创建追加式结构化需求快照。`requirements_status: sufficient_for_planning` 且 `active_requirements` 有效后，才能进入 Planner。视觉类 `undecided` 不继续采访，交给 Design Exploration。模板见 `templates/original_request.md`、`templates/requirements_interview.md` 和 `templates/requirements_snapshot.yaml`。

### 产品确认流程

Planner 在任何开发计划前都必须先产出可审核的产品方案，并在用户明确确认后才能生成正式计划并交给 Generator。详细状态机、文件约定和交互示例见 `docs/PLANNER_APPROVAL_WORKFLOW.md`；产品方案使用 `templates/product_proposal.md`。

### Design Exploration

当用户尚未确定 App 的视觉风格、页面布局，或明确希望先看参考方案时，Planner 必须先基于产品方案草稿生成一轮 3 个不同方向的静态设计预览。用户可以单选、融合、修改或要求新一轮方向。设计方向选定后，Planner 必须将其整合进新的产品方案版本，并再次等待用户明确确认；选择设计方向本身不构成开发批准。

设计说明使用 `templates/design_concept.md`，设计选择记录使用 `templates/design_selection.md`，跳过设计探索时使用 `templates/design_skip_decision.md`。预览仅写入项目的 `artifacts/design_previews/`，不得写入 Skill 目录或项目 `code/`。完整规范见 `DESIGN_EXPLORATION_WORKFLOW.md` 和 `docs/PLANNER_APPROVAL_WORKFLOW.md`。

- 状态协议与迁移规则：读取 `docs/workflow_protocol.md`。
- 项目目录、命名和隔离规则：读取 `docs/project_conventions.md`。
- 新建工件时：从 `templates/` 选择对应模板。
- 评估时：从 `config/evaluation_rules/` 读取 `project.yaml` 中 `evaluation_profile` 对应的 Profile。
