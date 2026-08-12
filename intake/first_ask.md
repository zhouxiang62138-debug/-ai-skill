# First-Ask Intake Module

> 模块标识：`first_ask_intake`  
> 类型：Planner 之前的需求采访模块，不是 Agent  
> 当前状态：正式协议为 workflow v7 与 project schema v7；v3-v6 只作为迁移兼容版本
> 角色约束：不得注册为第四个 `next_role`

## 0. 正式需求发现路由（v7）

需求发现不是一个新的 Agent，而是 First-Ask Intake 与可选 Module 的确定性路由：

```text
Initial Request
→ Intent Analysis
→ Research Gate
├─ required → REQUIREMENT_RESEARCH → INTAKE
├─ optional → 按 Research Gate 与用户需求决定是否执行
└─ not_required → 不执行外部 Research
→ Coverage Map
→ Gap Analysis
→ 1–3 High Value Questions per Round
→ Sufficiency Gate
→ optional REFERENCE_ANALYSIS
→ PLANNING
```

`每轮最多 1–3 个问题`只限制单轮提问数量，不限制整个项目的总轮数；总轮数由
Sufficiency Gate、冲突和用户决策共同决定。Research 与 Reference Analysis 都是
Module，不得写入 `next_role`，也不得被当作第四个 Agent。

Research Gate 不能被跳过；Research Execution 可以由确定性 Research Gate 判定为
`required`、`optional` 或 `not_required`。Research Gate 是必经的必要性判断，
但不是所有项目都必须执行外部 Research。

## 1. 目标

在 Planner 开始产品规划前，收集并结构化保存用户已经知道的事实、目标、约束和偏好，减少后续重复提问。

本模块只负责 Intake：

- 保存用户原始请求。
- 判断现有信息是否足以进入 Planner。
- 每轮提出少量高价值问题。
- 原样保存问题和用户回答。
- 创建追加式结构化需求快照。
- 把明确未决定的视觉问题路由给 Design Exploration。

本模块不负责：

- 决定最终产品范围。
- 划分 MVP。
- 设计产品方案、页面或 UI 风格。
- 生成设计预览。
- 生成正式计划。
- 编写代码。
- 执行测试或验收。
- 宣布 PASS 或 FAIL。
- 展示开发计划后自行开始工作。

## 2. 启动前置条件

每次运行前必须：

1. 定位唯一目标项目。
2. 读取项目根目录唯一的 `project.yaml`。
3. 确认没有 `memory/project.yaml` 或其他并行状态文件。
4. 只读取当前项目的数据。
5. 确认当前状态允许 Intake 运行。

如果根级 `project.yaml` 不存在，停止并报告 `missing_project_state`。本模块不得自行在 `memory/` 创建状态文件，也不得用聊天记录替代项目状态。

F3 接入后，本模块只应在以下状态运行：

- `INTAKE`
- 用户回复后的 `WAITING_FOR_REQUIREMENTS`

需求充分后必须退出模块：

```yaml
active_module: null
requirements_status: sufficient_for_planning
status: PLANNING
next_role: planner
```

## 3. 输入文件

按以下顺序读取：

1. 根级 `project.yaml`。
2. `active_requirements` 指向的需求快照（如有）。
3. `memory/requirements/request-<nnn>.md`。
4. `memory/requirements/interview-<nnn>.md`。
5. Planner 创建的 `memory/handoffs/intake-request-<nnn>.md`（如有）。

禁止读取其他项目的需求记录。

## 4. 输出文件

只允许在当前项目创建：

```text
memory/requirements/
├── request-<nnn>.md
├── interview-<nnn>.md
└── requirements_v<nnn>.yaml
```

使用模板：

- 原始请求：`templates/original_request.md`
- 采访轮次：`templates/requirements_interview.md`
- 需求快照：`templates/requirements_snapshot.yaml`

所有文件均为追加式历史工件，禁止覆盖。

## 5. 原始请求保护

首次收到用户需求时：

1. 创建下一个可用编号的 `request-<nnn>.md`。
2. 逐字保存用户原始请求。
3. 记录来源和时间。
4. 后续不得修改该文件。

用户后来补充或改变需求时：

- 把新原话保存在新的采访或请求记录中。
- 创建新的完整需求快照。
- 通过 `supersedes` 关联旧快照。
- 不回写旧请求、旧采访或旧快照。

## 6. 需求字段

至少维护：

- `target_users`
- `primary_goal`
- `use_cases`
- `platform`
- `required_features`
- `optional_features`
- `data_sources`
- `technical_constraints`
- `design_preferences`
- `undecided_items`
- `assumptions`
- `conflicts`
- `open_questions`

每个业务字段必须包含：

- `question_key`
- `value`
- `status`
- `source_refs`

使用假设时还必须包含：

- `assumption_reason`
- `decision_impact`

## 7. 字段状态

合法状态：

| 状态 | 使用条件 | 后续规则 |
| --- | --- | --- |
| `unanswered` | 尚无可用信息 | 仅在问题有当前价值时可询问 |
| `answered` | 用户已明确回答 | 禁止重复询问 |
| `assumed` | 采用可逆、低风险默认值 | 记录理由，不主动重复询问 |
| `undecided` | 用户明确表示尚未决定 | 禁止反复逼问，按类型路由 |
| `conflicting` | 用户陈述或来源互相矛盾 | 允许一次针对冲突的澄清 |
| `requires_user_decision` | 没有安全默认值且显著影响方向 | 必须等待用户决定 |
| `not_applicable` | 当前产品不适用 | 禁止询问 |

### 7.1 状态转换

允许的主要转换：

```text
unanswered → answered
unanswered → assumed
unanswered → undecided
unanswered → not_applicable
answered → conflicting
assumed → answered
undecided → answered
conflicting → answered
conflicting → requires_user_decision
requires_user_decision → answered
```

禁止：

- 无新证据把 `answered` 降为 `unanswered`。
- 把用户原话改写成假设。
- 把 `undecided` 自动改成 `answered`。
- 为了补齐模板重复询问 `answered` 或 `not_applicable`。

## 8. `undecided` 路由

用户明确说“不确定”时，必须记录为 `undecided`。

视觉类问题：

- UI 风格
- 主色
- 页面布局
- 信息密度
- 动效偏好

处理：

```yaml
design_preferences:
  status: undecided
  specification_completeness: none

undecided_items:
  - field: design_preferences
    routing: design_exploration
```

视觉类 `undecided` 不阻塞 `sufficient_for_planning`，也不得继续需求采访。

当用户提供设计规范时，First-Ask 只记录完整度事实，不决定是否跳过探索：

- `none`：没有可执行的视觉、布局或关键页面规范。
- `partial`：只有零散风格、配色或局部页面描述。
- `complete`：视觉系统、页面布局、信息层级和关键页面均有明确规范。

只有 `complete` 才允许 Planner 请求用户确认是否跳过；跳过决定仍由 Planner
记录，First-Ask 不得代替用户批准。

以下方向性问题通常不能长期保持 `undecided`：

- 核心目标。
- 主要目标用户。
- 目标平台。
- 强制合规要求。
- 核心数据来源。
- 首版必须功能。

如果没有安全默认值，将其设为 `requires_user_decision`。

## 9. 提问前去重

每次提出问题前：

1. 读取最新需求快照。
2. 收集所有历史 `question_key`。
3. 比较候选问题对应的字段和语义主题。
4. 删除已经回答或已有处理策略的问题。
5. 仅保留会显著影响产品方向的问题。

同一 `question_key` 在以下状态时禁止再次提问：

- `answered`
- `assumed`
- `undecided`
- `not_applicable`

仅以下状态允许后续提问：

- `conflicting`
- `requires_user_decision`
- `unanswered` 且问题仍具有高价值

稳定键示例：

```text
target_users.primary
primary_goal.main
use_cases.core
platform.primary
required_features.must_have
data_sources.primary
technical_constraints.hard_limits
design_preferences.explicit
```

不得通过换一种说法绕过去重规则。

## 10. 问题选择与轮次限制

每轮最多提出 1～3 个问题。不得一次发送长问卷。

优先级：

1. 会阻止 Planner 正确理解核心目标的问题。
2. 会改变目标平台、数据方案或必须功能的问题。
3. 相互冲突且无法安全推断的问题。
4. 会显著改变产品范围的问题。
5. 其他可由低风险假设处理的问题不主动询问。

一个问题只处理一个清晰主题。只有紧密相关且用户可一次回答的信息才能放在同一问题中。

问题应：

- 简短。
- 使用用户能理解的语言。
- 说明为什么需要该信息时保持简洁。
- 允许用户回答“不确定”。
- 不诱导用户接受模块偏好的答案。

## 11. 输入适配

模块先生成问题，再选择输入适配器。问题协议不得依赖特定扩展。

### 11.1 Joyride

如果 `joyride_request_human_input` 可调用：

- 优先使用 Joyride 展示本轮问题。
- 每轮仍受 1～3 个问题限制。
- 用户原始回答必须写入采访记录。

### 11.2 普通会话

Joyride 不可用时：

- 通过普通会话向用户提出相同问题。
- 设置 `status: WAITING_FOR_REQUIREMENTS`。
- 停止并等待用户下一条消息。
- 不得因缺少 Joyride 跳过 Intake。

### 11.3 无可用输入通道

如果任何输入方式都不可用：

```yaml
status: WAITING_FOR_REQUIREMENTS
requirements_status: waiting_user
blocked_reason: intake_input_adapter_unavailable
next_role: null
```

不得继续 Planner。

## 12. 采访记录规则

提出问题时创建新的 `interview-<nnn>.md`，记录：

- 输入需求版本。
- 输入适配器。
- 每个 `question_key`。
- 提问原文。
- 提问原因。
- 提问前字段状态。

收到回答后，在同一采访轮次完成回答区域。采访轮次在等待用户期间可以从“等待回答”完成为“已收到回答”，但完成后不得再次修改。若运行环境要求严格不可变，则问题和回答分别创建连续编号的追加记录，并在需求快照中同时引用。

不得：

- 把模型总结冒充用户原话。
- 删除用户含糊或矛盾的原始回答。
- 用假设填入“用户回答原文”。

## 13. 需求快照规则

收到有效回答或形成必要假设后：

1. 读取上一版完整快照。
2. 复制所有仍有效字段。
3. 应用本轮有来源的状态变化。
4. 保留冲突的全部来源。
5. 创建新的 `requirements_v<nnn>.yaml`。
6. 验证 YAML 可解析。
7. 验证版本号和文件名一致。
8. 更新 `active_requirements`。

快照必须是完整状态，不得只记录差异。

## 14. 足以进入 Planner 的判定

以下信息必须足以支撑产品规划：

- 目标用户。
- 核心目标。
- 至少一个主要使用场景。
- 目标平台，或有明确的低风险平台假设。
- 首版必须功能的边界。
- 适用时的数据来源。
- 已知硬性技术限制，或明确记录“目前无已知限制”。

允许：

- `design_preferences: undecided`
- 非关键可选功能为 `assumed` 或 `undecided`
- 不适用字段为 `not_applicable`

禁止进入 Planner：

- 任一关键字段为 `conflicting`。
- 任一关键字段为 `requires_user_decision`。
- 核心目标仍为 `unanswered`。
- 没有可识别的目标用户或使用场景。

满足条件时：

```yaml
requirements_status: sufficient_for_planning
active_module: null
status: PLANNING
next_role: planner
blocked_reason: null
```

否则：

```yaml
requirements_status: waiting_user
status: WAITING_FOR_REQUIREMENTS
next_role: null
```

## 15. 权限边界

允许读取：

- 根级 `project.yaml`
- `memory/requirements/`
- `memory/handoffs/intake-request-<nnn>.md`

允许写入：

- `memory/requirements/request-<nnn>.md`
- `memory/requirements/interview-<nnn>.md`
- `memory/requirements/requirements_v<nnn>.yaml`
- `project.yaml` 中 Intake 所有权字段

禁止写入：

- `code/`
- `memory/proposals/`
- `memory/plans/`
- `artifacts/design_previews/`
- `evaluation/`
- Skill 配置与 Prompt
- Planner、Generator、Evaluator 所有权字段

## 16. 与 Planner 的交接

交给 Planner 时，`project.yaml` 必须至少提供：

```yaml
requirements_status: sufficient_for_planning
requirements_version: <正整数>
active_requirements: memory/requirements/requirements_v<nnn>.yaml
active_interview: null
active_module: null
status: PLANNING
next_role: planner
```

Planner 必须把 `active_requirements` 作为产品方案来源，不得修改 Intake 文件。

如果 Planner 后续发现基础事实需要重新确认，应创建新的 `memory/handoffs/intake-request-<nnn>.md`。First-Ask 通过新的采访轮次和需求快照响应，不覆盖旧输出。

## 17. 停止条件

出现以下任一情况必须停止：

- 已提出本轮问题，等待用户回答。
- 存在关键冲突。
- 存在必须由用户决定的关键字段。
- 输入适配器不可用。
- 根级 `project.yaml` 缺失。
- 目标项目不唯一。
- 请求要求读取未经授权的其他项目。

本模块不得在停止后自行进入 Planner。
