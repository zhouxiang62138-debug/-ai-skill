# First-Ask Intake Module 集成设计

> 文档状态：待用户确认  
> 适用范围：AI Development Team Skill 的需求入口与产品规划流程升级  
> 当前阶段：仅设计，不实施  
> 核心约束：只保留 Planner、Generator、Evaluator 三个 Agent；First-Ask 是 Intake Module，不是第四个 Agent。

## 0. 设计结论

First-Ask 应作为文件驱动协议中的前置需求采访模块，在 Planner 之前运行。它只收集事实、目标和约束，并把结果追加保存到当前项目的 `memory/requirements/`。完成采访后，Planner 读取最新结构化需求快照，开展产品方案、Design Exploration 和批准流程。

为了不破坏三 Agent 架构：

- `next_role` 的合法非空值仍然只有 `planner`、`generator`、`evaluator`。
- First-Ask 通过 `active_module: first_ask_intake` 标识，不写入 `next_role`。
- `INTAKE` 和 `WAITING_FOR_REQUIREMENTS` 由 First-Ask Intake Module 处理。
- 需求达到 `sufficient_for_planning` 后，才进入 `PLANNING` 并设置 `next_role: planner`。
- First-Ask 不生成产品方案、设计方向、正式计划、代码或验收结论。

当前已安装的 First-Ask Skill 只有 `SKILL.md`，行为依赖 Joyride 的 `joyride_request_human_input`。集成时不能假设 Joyride 永远可用，应设计输入适配层：

1. Joyride 可用时，优先用 Joyride 分轮提问。
2. Joyride 不可用但普通会话输入可用时，使用普通会话提问，保持相同的轮次和记录协议。
3. 没有可用输入通道时，进入 `WAITING_FOR_REQUIREMENTS`，记录 `blocked_reason: intake_input_adapter_unavailable`，禁止静默跳过采访。

## 1. 模块职责边界

### 1.1 First-Ask Intake

First-Ask Intake 负责询问和记录：

- 产品准备给谁使用。
- 核心目标是什么。
- 主要使用场景。
- 目标平台。
- 必须具备的功能。
- 已知技术限制。
- 数据来源。
- 用户明确提出的风格要求。
- 用户明确表示尚未确定的事项。

First-Ask 只负责收集：

- 可由用户直接陈述的事实。
- 用户目标和成功标准。
- 已知业务、平台、数据和技术约束。
- 用户明确提出的偏好。
- 用户明确标记为尚未确定的事项。

First-Ask 不负责：

- 决定最终功能范围。
- 划分 MVP 与非 MVP。
- 设计产品方案或信息架构。
- 替用户决定 UI 风格。
- 生成三种设计方向。
- 生成正式开发计划。
- 编写或修改代码。
- 执行测试或质量验收。
- 宣布 PASS 或 FAIL。
- 修改 Skill 配置、角色规则或评估规则。

### 1.2 Planner

Planner 负责：

- 先读取根级 `project.yaml`。
- 读取 `active_requirements` 指向的完整结构化需求快照。
- 读取相关采访记录和用户原始请求。
- 不重复询问 First-Ask 已经回答的问题。
- 根据需求设计产品方案。
- 识别产品设计过程中新增且会显著影响方向的决策。
- 只询问 First-Ask 无法预见的产品决策问题。
- 对低风险缺口采用明确记录的默认假设。
- 生成 `product_proposal_v<nnn>.md` 产品方案草稿。
- 用户未确定风格时生成三种明显不同的设计方向。
- 接收用户的选择、融合和修改意见。
- 每次重大修改创建新的完整产品方案版本。
- 等待用户明确批准当前方案。
- 批准后生成正式 `plan-<nnn>.md`。

Planner 不得修改或覆盖 First-Ask 已生成的需求快照和采访记录。Planner 新发现的产品决策写入 `memory/decisions/`；如果用户明确改变了基础需求事实，则通过追加式 Intake 重新进入记录新版本，而不是回写旧文件。

### 1.3 Generator 与 Evaluator

Generator 只执行获批正式计划：

- 不读取未批准产品方案作为正式实施依据。
- `active_plan` 为空时不得开始开发。
- 不得自行改变获批产品范围。
- 不得把 Intake 记录中的可选想法自动提升为开发任务。

Evaluator 继续只按正式计划、验收标准和可复现证据验收，不修改需求、产品方案、设计选择、计划或代码。

## 2. 完整用户流程

```text
用户提出一句话需求
  ↓
First-Ask Intake 读取已有需求记录并判断完整度
  ↓
信息不足：每轮提出 1～3 个高价值问题
  ↓
将原始请求、逐轮回答和结构化需求快照追加保存
  ↓
需求达到 sufficient_for_planning
  ↓
Planner 读取 active_requirements
  ↓
Planner 生成产品方案草稿
  ↓
用户未确定设计风格或布局
  ↓
Planner 生成三种明显不同的设计方向或静态预览
  ↓
用户选择、融合、修改或要求重新生成
  ↓
Planner 创建新版本产品方案
  ↓
用户明确批准当前方案
  ↓
Planner 记录批准并生成正式 plan
  ↓
Generator 校验门禁并开始实现
  ↓
Evaluator 使用可复现证据验收
```

示例入口：

> 用户：“我要做一个数据分析报表系统。”

First-Ask 不立即要求用户完成一份长问卷，而是先读取现有记录，再优先询问会直接改变产品方向的少量问题，例如：

1. 主要用户是谁，他们最常用报表解决什么决策问题？
2. 目标平台是什么，数据来自现有数据库、文件上传还是第三方接口？
3. 哪些能力必须首版具备；是否有权限、隐私或部署限制？

如果用户回答“风格还不确定”，First-Ask 将 `design_preferences` 标记为 `undecided`，不继续追问审美细节。该问题由 Planner 的 Design Exploration 处理。

## 3. 防止重复提问机制

### 3.1 提问前置检查

First-Ask 每次提问前必须：

1. 读取根级 `project.yaml`。
2. 读取 `active_requirements`。
3. 读取所有比当前快照更新的采访或用户反馈记录。
4. 为候选问题匹配已有字段和语义主题。
5. 删除已经得到有效答案的问题。
6. 每轮只保留 1～3 个真正影响方向的问题。

禁止一次提交长问卷，也禁止为了“填满模板”追问不会影响产品方向的细节。

### 3.2 字段状态

每个需求字段必须包含独立状态。合法值如下：

| 状态 | 含义 | 是否允许重复询问 |
| --- | --- | --- |
| `unanswered` | 尚未询问或没有任何可用信息 | 可以，但必须具有当前价值 |
| `answered` | 用户已明确回答 | 禁止重复询问 |
| `assumed` | 使用了低风险默认值，且已记录理由 | 不主动重复询问；方案审核时允许用户修正 |
| `undecided` | 用户明确表示尚未确定 | 禁止反复逼问；按问题类型路由 |
| `conflicting` | 存在互相矛盾的用户陈述或来源 | 可以询问一次用于消除冲突 |
| `requires_user_decision` | 无安全默认值，且会显著改变产品方向 | 必须等待用户决定 |
| `not_applicable` | 对当前产品不适用 | 禁止询问 |

`undecided` 的路由规则：

- 视觉风格、配色、布局、信息密度：交给 Design Exploration。
- 非关键可选功能：记录到开放项或非 MVP 候选，不阻塞 Planner。
- 目标平台、强制合规、核心数据来源等方向性问题：升级为 `requires_user_decision`。

### 3.3 Planner 去重规则

Planner 必须：

- 先读取 `active_requirements`，再提出任何问题。
- 不得换一种说法重复询问 `answered` 字段。
- 不得重新询问已有明确答案，只因为 Planner 偏好不同表达。
- 只能询问产品设计过程中新增且会显著影响范围、流程或风险的问题。
- 对可逆、低风险的问题采用默认值，并写入产品方案的“风险与假设”。
- 把用户明确说“不确定”的视觉问题送入 Design Exploration。
- 把新的产品决策记录到 `memory/decisions/planning-decision-<nnn>.md`，不得覆盖 Intake 输出。

如果 Planner 发现基础事实发生变化，应创建 `memory/handoffs/intake-request-<nnn>.md`，说明需要补充的事实，再进入 `INTAKE`。First-Ask 根据该交接创建新的采访和需求快照版本。双方不得编辑对方的历史工件。

### 3.4 问题去重键

建议每个问题带稳定主题键，例如：

```yaml
question_key: platform.primary
field: platform
asked_in: memory/requirements/interview-001.md
answer_status: answered
```

同一 `question_key` 在状态为 `answered`、`assumed`、`undecided` 或 `not_applicable` 时不得再次提问。只有状态为 `conflicting` 或 `requires_user_decision` 时，才允许生成有针对性的后续问题。

## 4. 文件结构

### 4.1 Skill 本体

基于当前实际架构，建议增加 Intake 说明和需求模板，同时保留现有评估 Profile 目录：

```text
ai-development-team-skill/
├── SKILL.md
├── AGENTS.md
├── FIRST_ASK_INTEGRATION_DESIGN.md
├── DESIGN_EXPLORATION_WORKFLOW.md
├── intake/
│   └── first_ask.md
├── prompts/
│   ├── planner_prompt.md
│   ├── generator_prompt.md
│   └── evaluator_prompt.md
├── config/
│   ├── workflow.yaml
│   ├── role_policies.yaml
│   └── evaluation_rules/
├── docs/
│   ├── workflow_protocol.md
│   ├── project_conventions.md
│   └── PLANNER_APPROVAL_WORKFLOW.md
└── templates/
    ├── original_request.md
    ├── requirements_interview.md
    ├── requirements_snapshot.yaml
    ├── product_proposal.md
    ├── design_concept.md
    ├── design_selection.md
    └── plan.md
```

`intake/first_ask.md` 是模块协议，不是 Agent Prompt，也不得注册为第四个角色。

### 4.2 项目实例

沿用当前下划线和三位编号约定，避免无意义迁移：

```text
ai-projects/<project_id>/
├── project.yaml
├── code/
├── memory/
│   ├── requirements/
│   │   ├── request-001.md
│   │   ├── interview-001.md
│   │   ├── interview-002.md
│   │   ├── requirements_v001.yaml
│   │   └── requirements_v002.yaml
│   ├── proposals/
│   │   ├── product_proposal_v001.md
│   │   └── product_proposal_v002.md
│   ├── decisions/
│   │   ├── planning-decision-001.md
│   │   ├── design-selection-001.md
│   │   └── product-approval-001.md
│   ├── plans/
│   │   └── plan-001.md
│   └── handoffs/
│       └── intake-request-001.md
├── evaluation/
│   └── reports/
└── artifacts/
    └── design_previews/
        └── round_001/
            ├── concept_01/
            ├── concept_02/
            └── concept_03/
```

约束：

- Skill 目录不得保存任何具体项目需求或采访结果。
- 所有项目数据只写入当前项目目录。
- 一个项目只能有一个根级 `project.yaml`。
- 不创建 `memory/project.yaml` 或其他并行状态文件。
- 原始请求、采访、需求快照、产品方案、决策和计划全部追加保存，禁止覆盖。

## 5. First-Ask 输出格式

### 5.1 原始请求

首次收到需求时创建 `memory/requirements/request-001.md`，原样保存用户请求、来源和时间。该文件不可修改。后续需求变化通过新的采访、反馈和需求快照表达，不回写原始请求。

### 5.2 采访记录

每轮创建 `interview-<nnn>.md`，至少记录：

- 本轮读取的需求版本。
- 每个 `question_key`。
- 提问原文。
- 用户回答原文。
- 字段状态变化。
- 本轮结束原因。
- 时间和输入方式。

### 5.3 结构化需求快照

每次有效回答、假设确认或冲突处理后创建新的 `requirements_v<nnn>.yaml`。建议模板：

```yaml
requirement_version: 1
original_request:
  ref: memory/requirements/request-001.md
  text: 我要做一个数据分析报表系统

target_users:
  value: 企业运营和管理人员
  status: answered
  source_refs:
    - memory/requirements/interview-001.md#answer-01

primary_goal:
  value: 汇总业务数据并快速发现异常趋势
  status: answered
  source_refs:
    - memory/requirements/interview-001.md#answer-02

use_cases:
  value:
    - 查看核心指标总览
    - 按时间和部门筛选数据
    - 导出报表
  status: answered
  source_refs:
    - memory/requirements/interview-001.md#answer-03

platform:
  value:
    - web
  status: answered
  source_refs:
    - memory/requirements/interview-001.md#answer-04

required_features:
  value:
    - 仪表盘
    - 筛选器
    - 报表导出
  status: answered
  source_refs:
    - memory/requirements/interview-002.md#answer-01

optional_features:
  value:
    - 定时邮件
  status: assumed
  assumption_reason: 首版不阻塞，暂列为非 MVP 候选

data_sources:
  value:
    - existing_mysql_database
  status: answered
  source_refs:
    - memory/requirements/interview-001.md#answer-05

technical_constraints:
  value:
    - 部署在企业内网
  status: answered
  source_refs:
    - memory/requirements/interview-002.md#answer-02

design_preferences:
  value: null
  status: undecided
  source_refs:
    - memory/requirements/interview-002.md#answer-03

undecided_items:
  - field: design_preferences
    routing: design_exploration

assumptions:
  - field: optional_features
    value: 定时邮件暂不纳入 MVP
    risk: low

conflicts: []
open_questions: []

requirements_status: sufficient_for_planning
created_at: <ISO-8601 timestamp>
supersedes: memory/requirements/requirements_v001.yaml
```

### 5.4 字段更新规则

- `requirement_version`：每创建一个完整新快照递增；必须与文件名一致。
- `original_request`：始终引用并保留最初原文，不覆盖。
- 业务字段：每个字段包含 `value`、`status` 和来源。
- 用户给出明确答案：新快照将字段设为 `answered`。
- 使用低风险默认值：设为 `assumed`，必须记录理由和风险。
- 用户说“不确定”：设为 `undecided`，不得继续逼问同一问题。
- 新答案与旧答案冲突：设为 `conflicting`，同时保留所有来源。
- 无安全默认值且影响方向：设为 `requires_user_decision`。
- 字段不适用：设为 `not_applicable` 并记录判断依据。
- 所有变化通过新快照表达；旧快照永久保留。

### 5.5 聚合需求状态

`requirements_status` 的合法值：

| 值 | 含义 | 下一步 |
| --- | --- | --- |
| `draft` | 只有原始请求，尚未评估完整度 | First-Ask 评估 |
| `interviewing` | 正在形成问题或处理回答 | First-Ask 继续 |
| `waiting_user` | 已提出问题，等待用户 | 停止 |
| `sufficient_for_planning` | 关键事实足以让 Planner 开始 | 进入 `PLANNING` |
| `blocked_by_conflict` | 存在无法安全消解的关键冲突 | 等待用户 |

进入 `sufficient_for_planning` 不要求所有字段都为 `answered`。视觉问题可以为 `undecided`，低风险非关键项可以为 `assumed`，不适用项可以为 `not_applicable`。但任何影响核心目标、目标平台、必须功能、关键数据来源或强制约束的字段，不得处于 `conflicting` 或 `requires_user_decision`。

## 6. 产品方案与设计探索流程

Planner 必须先读取：

1. 根级 `project.yaml`。
2. `active_requirements` 指向的完整需求快照。
3. 原始请求和相关采访记录。
4. 已有产品决策、设计选择及最近评估报告（如有）。

然后创建：

```text
memory/proposals/product_proposal_v001.md
```

沿用当前实际命名，不改为 `product-proposal-001.md`。产品方案至少包含：

- 产品定位。
- 目标用户。
- 用户痛点。
- 核心使用流程。
- MVP 功能。
- 非 MVP 功能。
- 页面结构。
- 数据结构建议。
- 技术建议。
- 风险与假设。
- 待用户确认事项。
- 需求快照来源。

如果 `design_preferences.status: undecided`，或页面布局仍未确定，Planner 必须进入 Design Exploration，生成三种明显不同的方向。每个方向至少包含：

- 方案名称。
- 目标用户。
- 设计理念。
- 页面布局。
- 主色与辅助色。
- 信息密度。
- 交互特点。
- 适用场景。
- 优点。
- 缺点。

环境允许时生成静态 HTML/CSS：

```text
artifacts/design_previews/round_001/
├── concept_01/
│   ├── concept.md
│   ├── preview.html
│   └── preview.css
├── concept_02/
└── concept_03/
```

这些文件只是设计验证工件，不是生产代码：

- Planner 只能写入 `artifacts/design_previews/`。
- 不得写入 `code/`。
- Generator 不得修改设计预览。
- 未选择的预览不得成为正式实施依据。
- 选择设计方向不等于批准产品方案。

## 7. 用户修改和批准机制

用户可以：

- 选择一个设计方案。
- 融合多个方案。
- 修改功能。
- 修改页面结构。
- 修改配色或布局。
- 要求重新生成三个方向。
- 暂时不决定某些非阻塞细节。

处理规则：

- 单选或融合设计：创建 `design-selection-<nnn>.md`。
- 要求新方向：递增预览轮次，创建新的 `round_<nnn>`。
- 修改产品范围或页面结构：创建新的完整产品方案版本。
- 暂不决定非阻塞细节：记录为假设或开放项。
- 暂不决定会改变核心范围的事项：保持等待状态。

重大修改必须创建新版本：

```text
product_proposal_v001.md
product_proposal_v002.md
product_proposal_v003.md
```

禁止覆盖历史方案。

只有用户明确表达以下含义时才算批准：

- 确认方案。
- 批准方案。
- 按这个方案开发。
- 可以开始开发。
- 进入开发阶段。

以下模糊表达不得自动视为批准：

- 看起来不错。
- 差不多。
- 应该可以。
- 先这样吧。

遇到模糊表达时，Planner 只允许询问一次：“你是否明确批准当前 `active_proposal` 并准备进入开发？”未得到明确肯定前，`active_plan` 和 `approved_proposal` 必须保持 `null`。

## 8. `project.yaml` 状态设计

不得创建第二个状态文件。建议将根级 `project.yaml` 升级为新的 schema 版本，并增加 Intake 字段。

示例：

```yaml
schema_version: 3
project_id: <project_id>
status: INTAKE
next_role: null
active_module: first_ask_intake

requirements_status: draft
requirements_version: 0
active_requirements: null
active_interview: null
intake_round: 0

proposal_status: not_started
proposal_version: 0
active_proposal: null
approved_proposal: null

design_exploration_required: null
design_review_status: not_started
design_preview_round: 0
active_design_preview_round: null
selected_design_concept: null
design_selection_record: null

user_approval_status: not_requested
product_approval_record: null
active_plan: null

pending_feedback: null
current_iteration: 0
blocked_reason: null
next_role: null
```

字段定义：

| 字段 | 合法值或类型 | 更新时机与负责人 |
| --- | --- | --- |
| `status` | 状态机中的状态 | 当前执行者按门禁更新 |
| `next_role` | `planner`、`generator`、`evaluator`、`null` | 只指三个 Agent；First-Ask 不写角色名 |
| `active_module` | `first_ask_intake`、`null` | Intake 开始或结束时更新 |
| `requirements_status` | `draft`、`interviewing`、`waiting_user`、`sufficient_for_planning`、`blocked_by_conflict` | First-Ask 更新 |
| `requirements_version` | 非负整数 | 新需求快照创建后递增 |
| `active_requirements` | 路径或 `null` | 指向最新完整需求快照 |
| `active_interview` | 路径或 `null` | 已提出问题、等待回答时更新 |
| `intake_round` | 非负整数 | 每创建一轮采访时递增 |
| `proposal_status` | `not_started`、`draft`、`waiting_user_review`、`revision_requested`、`approved` | Planner 更新 |
| `proposal_version` | 非负整数 | 每创建完整产品方案版本后递增 |
| `active_proposal` | 路径或 `null` | Planner 指向最新待审核方案 |
| `approved_proposal` | 路径或 `null` | 用户明确批准后设置 |
| `design_exploration_required` | `true`、`false`、`null` | Planner 根据需求快照和用户确认设置 |
| `design_review_status` | `not_started`、`generating`、`waiting_user_selection`、`revision_requested`、`direction_selected`、`integrated_into_proposal`、`skipped_by_user` | Planner 更新 |
| `design_preview_round` | 非负整数 | 每创建一轮三方向预览时递增 |
| `active_design_preview_round` | 路径或 `null` | 指向当前待审核轮次 |
| `selected_design_concept` | 结构化对象或 `null` | 用户单选或融合后设置 |
| `design_selection_record` | 路径或 `null` | 设计选择决策创建后设置 |
| `user_approval_status` | `not_requested`、`waiting_explicit_confirmation`、`approved`、`revision_requested` | Planner 更新 |
| `product_approval_record` | 路径或 `null` | 用户明确批准后设置 |
| `active_plan` | 路径或 `null` | 所有批准门禁满足后设置 |
| `current_iteration` | `0..5` | Evaluator 返工时递增；达到 5 后停止 |
| `blocked_reason` | 字符串或 `null` | 无法继续时记录可诊断原因 |

建议将现有字段做明确迁移：

| 当前字段 | 新字段 |
| --- | --- |
| `product_approval_status` | `user_approval_status` |
| `preview_round` | `design_preview_round` |
| `active_preview_round` | `active_design_preview_round` |
| `requirement_source` | `active_requirements`，原值保留在迁移记录中 |

不得在未读取和迁移具体项目的情况下批量改写现有项目状态。

## 9. 状态机

### 9.1 完整状态流

```text
INTAKE
  ├─ 信息不足 → WAITING_FOR_REQUIREMENTS
  │                └─ 用户回答 → INTAKE
  └─ 信息充分 → PLANNING

PLANNING
  ├─ 需要设计探索 → DESIGN_EXPLORATION
  │                    └─ WAITING_FOR_DESIGN_REVIEW
  │                          ├─ 新方向 → DESIGN_EXPLORATION
  │                          └─ 单选/融合 → PLANNING_REVISION
  └─ 用户明确同意跳过设计探索 → WAITING_FOR_PRODUCT_REVIEW

PLANNING_REVISION
  ├─ 需要重新比较设计 → DESIGN_EXPLORATION
  └─ 新方案完成 → WAITING_FOR_PRODUCT_REVIEW

WAITING_FOR_PRODUCT_REVIEW
  ├─ 修改 → PLANNING_REVISION
  └─ 明确批准 → APPROVED_FOR_IMPLEMENTATION

APPROVED_FOR_IMPLEMENTATION
  → IMPLEMENTING
  → EVALUATING
      ├─ 可返工 → PLANNING / IMPLEMENTING
      ├─ 通过 → ACCEPTED
      └─ 达到迭代上限 → WAITING_FOR_USER

任意阶段：
  环境或协议阻塞 → BLOCKED
  需要用户处理非标准事项 → WAITING_FOR_USER
  已完成归档 → ARCHIVED
```

### 9.2 状态责任表

| 状态 | 执行者 | `next_role` | 是否必须停止等待用户 | 允许输出 |
| --- | --- | --- | --- | --- |
| `INTAKE` | First-Ask Intake Module | `null` | 否 | 原始请求、采访、需求快照 |
| `WAITING_FOR_REQUIREMENTS` | 用户；First-Ask 在回复后恢复 | `null` | 是 | 等待期间不得新增采访结论 |
| `PLANNING` | Planner | `planner` | 否 | 产品方案草稿 |
| `DESIGN_EXPLORATION` | Planner | `planner` | 否 | 三方向设计说明和预览 |
| `WAITING_FOR_DESIGN_REVIEW` | 用户；Planner 在回复后恢复 | `planner` | 是 | 等待期间不得推定选择 |
| `PLANNING_REVISION` | Planner | `planner` | 否 | 新版产品方案或新轮预览 |
| `WAITING_FOR_PRODUCT_REVIEW` | 用户；Planner 在回复后恢复 | `planner` | 是 | 等待期间不得创建正式计划 |
| `APPROVED_FOR_IMPLEMENTATION` | Generator 只做门禁校验 | `generator` | 否 | 校验结果、可选交接 |
| `IMPLEMENTING` | Generator | `generator` | 否 | 代码、证据、交接 |
| `EVALUATING` | Evaluator | `evaluator` | 否 | 验收报告 |
| `WAITING_FOR_USER` | 用户 | `null` | 是 | 阻塞说明 |
| `BLOCKED` | 无 | `null` | 是 | 阻塞说明 |
| `ACCEPTED` | 无 | `null` | 是 | 最终验收状态 |
| `ARCHIVED` | 无 | `null` | 是 | 归档状态 |

`WAITING_FOR_DESIGN_REVIEW`、`WAITING_FOR_PRODUCT_REVIEW` 的 `next_role: planner` 表示用户回复后由 Planner 处理，不代表允许自动运行。状态本身是强制停止门禁。

### 9.3 与当前状态的迁移映射

| 当前状态 | 新状态 |
| --- | --- |
| `DESIGN_REVIEW` | `WAITING_FOR_DESIGN_REVIEW` |
| `PRODUCT_REVIEW` | `WAITING_FOR_PRODUCT_REVIEW` |
| `PLANNING_COMPLETE` | `APPROVED_FOR_IMPLEMENTATION` |

过渡期可以只读识别旧状态并提示迁移，但新工件必须使用新状态。不得在未经用户确认的具体项目中自动重写旧状态。

## 10. 权限边界

### 10.1 First-Ask Intake

允许读取：

- 当前项目根级 `project.yaml`。
- 当前项目 `memory/requirements/`。
- Planner 发起的 `memory/handoffs/intake-request-<nnn>.md`。

允许写入：

- 当前项目 `memory/requirements/request-<nnn>.md`。
- 当前项目 `memory/requirements/interview-<nnn>.md`。
- 当前项目 `memory/requirements/requirements_v<nnn>.yaml`。
- `project.yaml` 中 Intake 所有权字段。

禁止：

- 写入 `code/`。
- 写入 `memory/proposals/`。
- 创建 `memory/plans/` 中的正式计划。
- 写入设计预览。
- 修改 Skill 配置或 Prompt。
- 修改评估规则。
- 修改 Planner、Generator 或 Evaluator 所有权字段。

### 10.2 Planner

允许：

- 读取 `memory/requirements/`。
- 读取 `active_requirements`。
- 写入 `memory/proposals/`、`memory/decisions/`、`memory/plans/` 和允许的交接目录。
- 写入 `artifacts/design_previews/`。
- 更新产品规划、设计审核和用户批准字段。

禁止：

- 修改 First-Ask 的需求快照或采访记录。
- 修改正式代码。
- 跳过用户明确批准。
- 把未批准方案交给 Generator。
- 将 `undecided` 视觉问题反复退回 Intake。

### 10.3 Generator

开始前必须验证：

- `status: APPROVED_FOR_IMPLEMENTATION`。
- `user_approval_status: approved`。
- `active_requirements` 非空。
- `approved_proposal` 非空。
- `product_approval_record` 非空。
- `active_plan` 非空且存在。
- 正式计划记录的需求、产品方案和设计选择来源与 `project.yaml` 一致。

禁止：

- 读取未批准产品方案作为正式实施依据。
- 在 `active_plan` 为空时开发。
- 自行改变用户批准的产品范围。
- 修改需求采访、产品方案、设计选择或评估标准。

### 10.4 字段所有权

| 字段组 | 唯一写入者 |
| --- | --- |
| `requirements_*`、`active_requirements`、`active_interview`、`intake_round` | First-Ask Intake |
| `proposal_*`、`active_proposal`、`approved_proposal` | Planner |
| `design_*`、`selected_design_concept` | Planner |
| `user_approval_status`、`product_approval_record`、`active_plan` | Planner；Generator 只校验 |
| `current_iteration`、`last_evaluation` | Evaluator |

跨边界信息只能通过追加式交接或决策文件传递，禁止互相覆盖输出。

## 11. 实际交互示例

### 11.1 用户提出一句话需求

用户：

> 我要做一个数据分析报表系统。

| 项目 | 值 |
| --- | --- |
| 状态 | `INTAKE` |
| `next_role` | `null` |
| `active_module` | `first_ask_intake` |
| 输入 | 用户原始请求 |
| 输出 | `memory/requirements/request-001.md` |
| 状态更新 | `requirements_status: draft`、`requirements_version: 0` |

### 11.2 First-Ask 提出少量问题

First-Ask：

> 1. 主要给哪些人使用，他们最常用报表解决什么问题？  
> 2. 系统运行在 Web、桌面还是移动端，数据主要从哪里来？  
> 3. 首版必须有哪些能力，是否存在权限、内网部署或数据合规限制？

| 项目 | 值 |
| --- | --- |
| 状态 | `WAITING_FOR_REQUIREMENTS` |
| `next_role` | `null` |
| 输入 | `request-001.md` |
| 输出 | `interview-001.md`，先记录问题 |
| 状态更新 | `requirements_status: waiting_user`、`intake_round: 1`、`active_interview: memory/requirements/interview-001.md` |

### 11.3 用户回答并形成需求快照

用户：

> 给运营和管理人员使用，做 Web 版，从现有 MySQL 读取数据。首版要有总览、筛选、趋势图和导出，需要内网部署。风格我还没想好。

First-Ask 创建结构化快照：

| 项目 | 值 |
| --- | --- |
| 状态 | `PLANNING` |
| `next_role` | `planner` |
| 输入 | `interview-001.md` 的用户回答 |
| 输出 | 完整的 `interview-001.md`、`requirements_v001.yaml` |
| 状态更新 | `requirements_status: sufficient_for_planning`、`requirements_version: 1`、`active_requirements: memory/requirements/requirements_v001.yaml`、`active_module: null` |

其中 `design_preferences.status: undecided`，不再由 First-Ask 追问。

### 11.4 Planner 生成产品方案草稿

Planner 读取需求后生成：

| 项目 | 值 |
| --- | --- |
| 状态 | `DESIGN_EXPLORATION` |
| `next_role` | `planner` |
| 输入 | `project.yaml`、`request-001.md`、`requirements_v001.yaml` |
| 输出 | `memory/proposals/product_proposal_v001.md` |
| 状态更新 | `proposal_status: draft`、`proposal_version: 1`、`active_proposal: memory/proposals/product_proposal_v001.md`、`design_exploration_required: true` |

### 11.5 Planner 生成三个设计方向

Planner 生成：

- `concept_01`：极简专业报表。
- `concept_02`：浅色模块化分析工作台。
- `concept_03`：深色高密度数据驾驶舱。

| 项目 | 值 |
| --- | --- |
| 状态 | `WAITING_FOR_DESIGN_REVIEW` |
| `next_role` | `planner` |
| 输入 | `product_proposal_v001.md`、`requirements_v001.yaml` |
| 输出 | `artifacts/design_previews/round_001/concept_01..03/` |
| 状态更新 | `design_review_status: waiting_user_selection`、`design_preview_round: 1`、`active_design_preview_round: artifacts/design_previews/round_001` |

Planner 停止并等待用户。

### 11.6 用户融合设计方向

用户：

> 选择方案一的整体布局，但趋势分析页使用方案三的高密度图表；整体配色保持浅色。

| 项目 | 值 |
| --- | --- |
| 状态 | `PLANNING_REVISION` |
| `next_role` | `planner` |
| 输入 | 用户反馈、`round_001` |
| 输出 | `memory/decisions/design-selection-001.md` |
| 状态更新 | `design_review_status: direction_selected`、`selected_design_concept.mode: blend`、`design_selection_record: memory/decisions/design-selection-001.md` |

### 11.7 Planner 创建新版产品方案

| 项目 | 值 |
| --- | --- |
| 状态 | `WAITING_FOR_PRODUCT_REVIEW` |
| `next_role` | `planner` |
| 输入 | `requirements_v001.yaml`、`product_proposal_v001.md`、`design-selection-001.md` |
| 输出 | `memory/proposals/product_proposal_v002.md` |
| 状态更新 | `proposal_status: waiting_user_review`、`proposal_version: 2`、`active_proposal: memory/proposals/product_proposal_v002.md`、`design_review_status: integrated_into_proposal`、`user_approval_status: waiting_explicit_confirmation` |

Planner 停止并等待明确批准。

### 11.8 用户明确批准

用户：

> 确认当前方案，可以开始开发。

Planner：

| 项目 | 值 |
| --- | --- |
| 状态 | `APPROVED_FOR_IMPLEMENTATION` |
| `next_role` | `generator` |
| 输入 | 用户批准、`product_proposal_v002.md`、`design-selection-001.md` |
| 输出 | `memory/decisions/product-approval-001.md`、`memory/plans/plan-001.md` |
| 状态更新 | `proposal_status: approved`、`approved_proposal: memory/proposals/product_proposal_v002.md`、`user_approval_status: approved`、`product_approval_record: memory/decisions/product-approval-001.md`、`active_plan: memory/plans/plan-001.md` |

### 11.9 Generator 实现

Generator 先校验需求、方案、批准记录和计划的来源链：

| 项目 | 值 |
| --- | --- |
| 状态 | `IMPLEMENTING` |
| `next_role` | `generator` |
| 输入 | `project.yaml`、`active_plan`、批准与设计选择记录 |
| 输出 | `code/`、验证证据、`memory/handoffs/handoff-001.md` |
| 状态更新 | 完成后 `status: EVALUATING`、`next_role: evaluator` |

### 11.10 Evaluator 验收

| 项目 | 值 |
| --- | --- |
| 状态 | `EVALUATING` |
| `next_role` | `evaluator` |
| 输入 | 正式计划、代码、交接、验证证据 |
| 输出 | `evaluation/reports/evaluation-001.md` |
| 状态更新 | PASS 时进入 `ACCEPTED`；可返工 FAIL 按问题类型路由；迭代达到 5 时进入 `WAITING_FOR_USER` |

## 12. 迁移方案

迁移严格分为 F1～F8。每个阶段完成后必须停止，向用户报告新增或修改的文件、验证证据和风险，并等待用户明确确认后才能进入下一阶段。

### F1：检查 First-Ask Skill 的现有内容与兼容性

工作：

- 读取已安装 First-Ask Skill。
- 记录 Joyride 依赖和可用性。
- 核对当前 AI Development Team 状态机、权限和命名。
- 输出兼容性检查记录。

门禁：用户确认兼容性结论后才能进入 F2。

### F2：设计 Intake 模块和模板

工作：

- 新增 `intake/first_ask.md`。
- 新增原始请求、采访和结构化需求快照模板。
- 定义字段状态、去重键和提问轮次限制。

禁止：修改 Planner Prompt 或工作流。

门禁：用户审核 Intake 输出格式后才能进入 F3。

### F3：修改工作流和状态字段

工作：

- 升级 `config/workflow.yaml`。
- 升级 `templates/project.yaml` schema。
- 增加 `active_module` 和 Intake 字段。
- 加入新等待状态和旧状态迁移映射。

门禁：YAML 解析和状态迁移检查通过，并经用户确认。

### F4：修改 Planner 读取与防重复规则

工作：

- 更新 Planner 先读 `active_requirements` 的规则。
- 禁止 Planner 覆盖或重复询问 Intake 输出。
- 建立 Planner 新产品决策与 Intake 重新进入机制。

禁止：把 First-Ask 全文直接复制进 `planner_prompt.md`。

门禁：重复提问场景测试通过，并经用户确认。

### F5：衔接产品方案批准与 Design Exploration

工作：

- 将 Intake 的 `undecided` 视觉问题路由到现有 Design Exploration。
- 保留三方向预览、选择、融合和最终批准双门禁。
- 校验 Generator 的批准来源链。

说明：现有 Design Exploration 已实现，本阶段以兼容和回归验证为主，不重复创建另一套流程。

门禁：用户确认需求采访、设计选择和产品批准之间没有越权。

### F6：建立测试项目

创建隔离测试项目：

```text
C:\Users\28388\Desktop\ai-projects\test_first_ask_integration
```

测试要求：

- 使用 `test_` 前缀。
- 不把测试数据写入 Skill 目录。
- 覆盖信息充分、信息不足、`undecided`、冲突、设计融合和模糊批准。
- 保留 `TEST_REPORT.md`。
- 测试结束后移动到 `archive/`；任何删除动作必须另行取得用户明确同意。

门禁：用户确认测试范围后才能创建测试项目。

### F7：验证完整流程

验证：

- First-Ask 不重复提问。
- Planner 不覆盖需求快照。
- `undecided` 视觉问题进入 Design Exploration。
- 未明确批准时无法创建正式计划。
- Generator 在 `active_plan: null` 时无法开发。
- Evaluator 有可复现证据才可 PASS。
- `current_iteration` 达到 5 时停止。

门禁：全部必需验证有可复现证据，并经用户确认。

### F8：输出迁移验证报告

输出：

- 迁移文件清单。
- 状态和字段变更。
- 测试覆盖与证据。
- 已知限制。
- 回滚说明。
- 最终 `TEST_REPORT.md`。

门禁：用户审核报告后，才可宣布迁移完成。

## 13. 本阶段禁止事项与确认门禁

本文件是待确认设计，不代表迁移已开始。本阶段禁止：

- 修改现有 AI Development Team Skill 文件。
- 修改 First-Ask Skill。
- 修改任何现有项目。
- 创建新的 Agent。
- 将 First-Ask 直接复制进 `planner_prompt.md`。
- 编写代码或控制脚本。
- 改变 Planner、Generator、Evaluator 三角色体系。
- 创建测试项目。

用户明确确认本设计后，也只能从 F1 开始。F1 完成后必须再次停止，不能自动进入 F2。
