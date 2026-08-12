# First-Ask 集成 F1 兼容性检查报告

> 阶段：F1  
> 状态：已完成，等待用户确认  
> 日期：2026-07-29  
> 结论：有条件兼容，无架构性阻塞  
> 下一阶段：用户明确确认后才可进入 F2

## 1. 检查范围

本阶段仅执行只读检查和兼容性分析，检查对象：

- 已安装 First-Ask Skill：
  - `C:\Users\28388\.codex\skills\first-ask\SKILL.md`
- 当前 AI Development Team Skill 工作目录：
  - `C:\Users\28388\Desktop\ai-development-team-skill`
- 已安装 AI Development Team Skill：
  - `C:\Users\28388\.codex\skills\ai-development-team-skill`
- 当前可发现的交互输入工具。
- 已批准设计：
  - `FIRST_ASK_INTEGRATION_DESIGN.md`

本阶段没有：

- 修改现有 Skill 文件。
- 修改已安装 First-Ask。
- 修改任何项目。
- 创建测试项目。
- 创建第四个 Agent。
- 进入 F2。

## 2. First-Ask 现状

### 2.1 文件清单

已安装 First-Ask 只有一个文件：

```text
first-ask/
└── SKILL.md
```

不存在：

- 独立 Intake 模块协议。
- 需求字段模板。
- 采访记录模板。
- 结构化需求快照模板。
- 状态机配置。
- 权限配置。
- 文件持久化规则。
- 重复提问检测规则。
- 与 Planner 的交接协议。
- 测试或验证资源。

### 2.2 当前行为

First-Ask 当前定义的是一个通用任务澄清流程：

- 理解任务范围和目标。
- 持续向用户提问。
- 澄清交付物、成功标准和技术限制。
- 信息充分后展示计划并开始工作。

它没有区分：

- 事实采访与产品决策。
- Intake 与 Planner。
- 需求记录与产品方案。
- 用户说“不确定”后的路由。
- 用户批准与开始执行的门禁。

因此不能把原始 First-Ask 全文直接复制进 `planner_prompt.md`，否则会让 Planner 重复采访，并让 First-Ask 越权进入计划和执行阶段。

## 3. Joyride 依赖检查

First-Ask 明确依赖：

```text
joyride_request_human_input
```

当前工具发现结果：

- 未发现 `joyride_request_human_input`。
- 未发现其他 Joyride 专用输入工具。
- 当前环境仍可通过普通会话接收用户回答。

兼容性判断：

| 项目 | 结论 |
| --- | --- |
| Joyride 作为唯一输入方式 | 不兼容 |
| Joyride 作为优先输入适配器 | 兼容 |
| 普通会话作为后备输入方式 | 可行 |
| Joyride 缺失时静默跳过 Intake | 禁止 |

F2 必须把提问逻辑与具体输入工具分离：

1. 模块先生成本轮 1～3 个高价值问题。
2. 输入适配器负责展示问题和接收回答。
3. Joyride 可用时优先使用。
4. Joyride 不可用时使用普通会话。
5. 没有可用输入方式时进入 `WAITING_FOR_REQUIREMENTS`，并记录可诊断原因。

## 4. AI Development Team 当前基线

### 4.1 角色体系

当前只注册三个角色：

- Planner
- Generator
- Evaluator

该结构与批准设计兼容。First-Ask 必须作为 `active_module: first_ask_intake` 运行，不得加入 `next_role` 的角色枚举。

### 4.2 状态机

当前 `config/workflow.yaml`：

- `version: 2`
- `INTAKE.next_role: planner`
- 已有 `PLANNING`
- 已有 `DESIGN_EXPLORATION`
- 已有 `DESIGN_REVIEW`
- 已有 `PRODUCT_REVIEW`
- 已有 `PLANNING_REVISION`
- 已有 `PLANNING_COMPLETE`
- 已有 Generator、Evaluator 和终止状态

主要兼容差距：

- `INTAKE` 当前直接交给 Planner，不支持前置模块。
- 缺少 `WAITING_FOR_REQUIREMENTS`。
- 缺少明确的 `active_module` 路由。
- 等待设计和等待产品审核使用旧状态名。
- 正式开发门禁使用 `PLANNING_COMPLETE`，批准设计建议使用 `APPROVED_FOR_IMPLEMENTATION`。

这些差距需要在 F3 处理，F1 不修改。

### 4.3 项目状态模板

当前 `templates/project.yaml`：

- `schema_version: 2`
- 没有 `active_module`
- 没有 `requirements_status`
- 没有 `requirements_version`
- 没有 `active_requirements`
- 没有 `active_interview`
- 没有 `intake_round`
- 只有较弱的 `requirement_source`

当前模板足以支持 Planner 产品方案与 Design Exploration，但不能可靠支持分轮采访、需求版本和字段所有权。

F3 需要设计 schema v3 迁移；不得创建第二个状态文件。

### 4.4 权限

当前 `config/role_policies.yaml`：

- 没有 First-Ask Intake 模块权限段。
- Planner 可读写 `memory/requirements/`。
- Planner 可写方案、决策、计划和设计预览。
- Generator 已有批准方案门禁和设计预览写保护。

兼容性问题：

- Planner 可写需求目录会破坏 Intake 的唯一写入者边界。
- First-Ask 没有被限制在需求目录和 Intake 字段。
- 当前权限格式只描述 Agent，尚未描述 Module。

F2 应先定义 Module 权限协议；F3/F4 再修改实际配置。

### 4.5 Planner

当前 Planner 已具备：

- 产品方案草稿。
- 三方向 Design Exploration。
- 设计选择与融合。
- 最终产品批准门禁。
- 批准后生成正式计划。

当前 Planner 尚缺：

- 强制读取 `active_requirements`。
- 字段级提问去重。
- 禁止覆盖 Intake 快照。
- `undecided` 字段路由。
- 基础事实变化时重新进入 Intake 的交接协议。

这些差距属于 F4，不影响 F1 的兼容结论。

### 4.6 文件命名

当前实际命名：

```text
memory/proposals/product_proposal_v001.md
artifacts/design_previews/round_001/concept_01/
memory/plans/plan-001.md
```

批准设计已经选择沿用这些命名。无需引入附件示例中的第二套连字符命名。

## 5. 工作目录与已安装版本一致性

对以下核心文件执行了 SHA-256 对比：

- `AGENTS.md`
- `SKILL.md`
- `config/workflow.yaml`
- `config/role_policies.yaml`
- `templates/project.yaml`
- `prompts/planner_prompt.md`

结果：

```text
全部一致
```

因此 F1 的分析基线与当前已安装 AI Development Team Skill 一致，不存在“只分析了工作副本、已安装版本仍不同步”的问题。

## 6. 兼容性矩阵

| 检查项 | 结论 | 说明 | 后续阶段 |
| --- | --- | --- | --- |
| 三 Agent 架构 | 兼容 | First-Ask 使用 Module 身份 | F2 |
| 文件驱动协议 | 兼容 | 需增加 `active_module` 和 Intake 字段 | F3 |
| 单一根级 `project.yaml` | 兼容 | 不创建第二状态文件 | F3 |
| 追加式历史工件 | 兼容 | 需求采访和快照必须版本化 | F2 |
| Design Exploration | 兼容 | `undecided` 视觉问题可直接路由 | F5 |
| 产品批准门禁 | 兼容 | 需补充需求来源链 | F5 |
| Joyride 硬依赖 | 不兼容 | 当前未发现 Joyride 工具 | F2 |
| 普通会话后备输入 | 兼容 | 需要输入适配规则 | F2 |
| 需求持久化 | 当前缺失 | First-Ask 没有文件模板 | F2 |
| 防重复提问 | 当前缺失 | First-Ask 和 Planner 都无字段级去重 | F2、F4 |
| Intake 状态路由 | 当前缺失 | `INTAKE` 目前直接进入 Planner | F3 |
| 字段写入所有权 | 部分冲突 | Planner 当前可写需求目录 | F3、F4 |
| Generator 门禁 | 部分兼容 | 已校验方案和设计，需加入需求快照 | F5 |
| 旧项目状态迁移 | 需要设计 | 禁止未经授权批量改写项目 | F3 |

## 7. 主要风险与控制措施

### R1：First-Ask 被误当成第四个 Agent

风险：破坏现有角色体系和 `next_role` 协议。

控制：

- 使用 `active_module: first_ask_intake`。
- `next_role` 非空值仍只有三个 Agent。
- Intake 完成后才设置 `next_role: planner`。

### R2：Joyride 不可用导致流程中断

风险：无法采访，或被错误地直接跳到 Planner。

控制：

- Joyride 只作为优先输入适配器。
- 普通会话作为后备。
- 无输入通道时进入明确等待状态。

### R3：重复提问

风险：First-Ask 和 Planner 对同一事实换一种说法再次询问。

控制：

- 使用字段状态和稳定 `question_key`。
- Planner 强制先读 `active_requirements`。
- `answered`、`assumed`、`undecided`、`not_applicable` 禁止重复询问。

### R4：双方覆盖输出

风险：Planner 修改需求快照，First-Ask 修改产品状态。

控制：

- 字段和目录设唯一写入者。
- 跨边界只使用追加式交接。
- 禁止覆盖历史文件。

### R5：视觉问题卡在 Intake

风险：用户说“不确定”后 First-Ask 反复追问风格。

控制：

- 视觉问题标记为 `undecided`。
- 需求仍可进入 `sufficient_for_planning`。
- Planner 在 Design Exploration 中解决。

### R6：旧项目被自动迁移

风险：破坏既有状态和历史工件。

控制：

- F3 只设计迁移映射。
- 未读取具体项目的根级 `project.yaml` 时不得修改。
- 每个项目迁移都需要明确授权和可恢复记录。

## 8. F2 推荐输入与输出

如果用户确认 F1，F2 只允许设计和新增 Intake 模块及模板，不修改 Planner Prompt 或工作流。

建议 F2 输出：

```text
intake/
└── first_ask.md

templates/
├── original_request.md
├── requirements_interview.md
└── requirements_snapshot.yaml
```

F2 必须定义：

- 每轮最多 1～3 个问题。
- 问题优先级。
- `question_key` 去重。
- 字段状态转换。
- 原始请求不可覆盖。
- 采访和快照追加版本。
- Joyride 与普通会话输入适配。
- `sufficient_for_planning` 判定。
- Intake 目录与字段权限边界。

F2 不允许：

- 修改 `config/workflow.yaml`。
- 修改 `templates/project.yaml`。
- 修改 `prompts/planner_prompt.md`。
- 修改任何项目。
- 创建测试项目。

## 9. F1 验收结果

| 验收项 | 结果 |
| --- | --- |
| 已读取 First-Ask 实际内容 | 通过 |
| 已记录 Joyride 依赖 | 通过 |
| 已检查 Joyride 当前可用性 | 通过；当前不可发现 |
| 已核对三 Agent 边界 | 通过 |
| 已核对状态机 | 通过 |
| 已核对权限 | 通过 |
| 已核对项目模板字段 | 通过 |
| 已核对 Planner 与 Design Exploration | 通过 |
| 已核对文件命名 | 通过 |
| 已核对工作副本与安装副本 | 通过 |
| 未修改现有 Skill | 通过 |
| 未修改项目或创建测试项目 | 通过 |

## 10. F1 结论与门禁

First-Ask 可以融入 AI Development Team，但必须采用 Intake Module 适配，而不是直接复用其“采访后自行计划和执行”的完整行为。

不存在需要增加第四个 Agent 的理由，也不存在需要重写现有 Design Exploration 的理由。主要工作集中在：

- 为 First-Ask 增加持久化、去重和输入适配协议。
- 为文件驱动状态机增加前置 Module 路由。
- 收紧 Planner 与 Intake 的写入边界。
- 把结构化需求来源加入产品方案与 Generator 门禁。

F1 已完成。未经用户明确确认，不得进入 F2。
