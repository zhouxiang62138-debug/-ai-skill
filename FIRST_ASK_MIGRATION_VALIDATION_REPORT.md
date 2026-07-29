# First-Ask 集成迁移验证报告

> 项目：AI Development Team Skill  
> 迁移范围：F1–F8  
> 报告日期：2026-07-30  
> 工作副本：`C:\Users\28388\Desktop\ai-development-team-skill`  
> 已安装副本：`C:\Users\28388\.codex\skills\ai-development-team-skill`  
> 结论：工作副本验证通过，等待用户最终审核；已安装副本尚未同步

## 1. 迁移目标与结论

本次迁移已在工作副本中把 First-Ask 适配为 Planner 之前的 `First-Ask Intake Module`，没有创建第四个 Agent，也没有替代 Planner。

迁移后的职责边界为：

- First-Ask Intake：收集事实、目标和约束，保存追加式需求记录，执行字段去重，将视觉 `undecided` 路由到 Design Exploration。
- Planner：读取活动需求快照，设计产品方案和三方向预览，处理用户选择与修改，获得明确批准后才生成正式计划。
- Generator：只在需求、获批方案、设计决策、批准记录和正式计划来源链完整时进入实现。
- Evaluator：只依据可复现证据验收；没有必需证据不得 PASS。

工作副本已经通过 schema、状态、权限、来源链和隔离测试验证。按照迁移门禁，本报告经用户审核前不宣告迁移完成，也不覆盖已安装 Skill。

## 2. 分阶段完成情况

| 阶段 | 完成内容 | 验证结果 |
| --- | --- | --- |
| F1 | 检查 First-Ask、Joyride、三 Agent 架构与现有协议兼容性 | 通过 |
| F2 | 新增 Intake 协议、原始请求、采访和需求快照模板 | 通过 |
| F3 | 工作流和项目模板升级到 schema v3，增加 Intake 状态与字段 | 通过 |
| F4 | Planner 强制读取 `active_requirements`，增加去重、回退 Intake 和字段所有权 | 通过 |
| F5 | 衔接 Design Exploration、产品明确批准与 Generator 完整来源链 | 通过 |
| F6 | 创建隔离测试项目和 T01–T11 测试计划 | 通过 |
| F7 | 执行完整协议测试并保留可复现证据 | 11/11 通过 |
| F8 | 输出迁移验证报告、最终测试记录并归档测试项目 | 通过，等待用户审核 |

## 3. 迁移文件清单

工作副本相对当前已安装副本共有 23 个新增或内容不同的文件。

### 3.1 新增文件

| 文件 | 用途 |
| --- | --- |
| `FIRST_ASK_INTEGRATION_DESIGN.md` | 经确认的完整集成设计与 F1–F8 门禁 |
| `FIRST_ASK_F1_COMPATIBILITY_REPORT.md` | First-Ask 与现有架构兼容性基线 |
| `intake/first_ask.md` | Intake Module 的输入、输出、去重、路由和权限协议 |
| `templates/original_request.md` | 不可覆盖的用户原始请求模板 |
| `templates/requirements_interview.md` | 分轮采访和 `question_key` 去重模板 |
| `templates/requirements_snapshot.yaml` | 完整结构化需求快照模板 |
| `templates/intake_request.md` | Planner 发现基础事实缺口时的回退交接 |
| `templates/planning_decision.md` | Planner 低风险假设和产品决策记录 |
| `templates/product_approval.md` | 用户明确批准与正式计划来源记录 |

### 3.2 修改文件

| 文件 | 主要变更 |
| --- | --- |
| `config/workflow.yaml` | 升级到 v3；加入 Intake、等待状态、批准状态和旧状态只读映射 |
| `config/role_policies.yaml` | 增加 Intake Module 权限、目录写保护和字段所有权 |
| `templates/project.yaml` | 升级到 schema v3，增加需求、方案、设计和批准字段 |
| `prompts/planner_prompt.md` | 强制读取活动需求、禁止重复提问和覆盖 Intake 输出 |
| `prompts/generator_prompt.md` | 校验完整获批来源链，`active_plan: null` 时禁止开发 |
| `templates/product_proposal.md` | 增加需求版本、设计选择和页面设计来源 |
| `templates/plan.md` | 增加需求、方案、批准和设计来源链 |
| `AGENTS.md` | 加入 Intake、Design Exploration 和双重确认共享规则 |
| `SKILL.md` | 说明 First-Ask Intake、产品确认与资源入口 |
| `agents/openai.yaml` | 更新 Skill 的用户可见元数据 |
| `docs/workflow_protocol.md` | 记录 schema v3 状态机、迁移和门禁 |
| `docs/PLANNER_APPROVAL_WORKFLOW.md` | 记录需求、设计选择和产品批准的双重确认流程 |
| `docs/project_conventions.md` | 记录需求目录、版本命名和项目隔离约束 |
| `DESIGN_EXPLORATION_WORKFLOW.md` | 衔接 `active_requirements` 与设计审核状态 |

说明：本报告自身为 F8 新增交付物，不计入上述执行前盘点得到的 23 个文件。

## 4. 状态机变更

### 4.1 新的主状态

```text
INTAKE
→ WAITING_FOR_REQUIREMENTS
→ PLANNING
→ DESIGN_EXPLORATION
→ WAITING_FOR_DESIGN_REVIEW
→ PLANNING_REVISION
→ WAITING_FOR_PRODUCT_REVIEW
→ APPROVED_FOR_IMPLEMENTATION
→ IMPLEMENTING
→ EVALUATING
→ ACCEPTED / WAITING_FOR_USER / BLOCKED
→ ARCHIVED
```

### 4.2 执行者与停止门禁

| 状态 | 执行者 | 门禁 |
| --- | --- | --- |
| `INTAKE` | `first_ask_intake` Module | `next_role: null` |
| `WAITING_FOR_REQUIREMENTS` | 等待用户 | 禁止进入 Planner |
| `PLANNING` | Planner | 必须有充分且可解析的 `active_requirements` |
| `WAITING_FOR_DESIGN_REVIEW` | 等待用户 | 禁止推定设计选择 |
| `WAITING_FOR_PRODUCT_REVIEW` | 等待用户 | `active_plan` 必须为空 |
| `APPROVED_FOR_IMPLEMENTATION` | Generator 入口校验 | 必须有明确批准和完整来源链 |
| `EVALUATING` | Evaluator | 无可复现证据不得 PASS |
| `WAITING_FOR_USER` | 等待用户 | 停止自动动作 |
| `ARCHIVED` | 无执行者 | 项目只读保留 |

### 4.3 旧状态兼容

旧状态保留为只读迁移别名，不用于创建新工件：

| 旧状态 | 新状态 |
| --- | --- |
| `DESIGN_REVIEW` | `WAITING_FOR_DESIGN_REVIEW` |
| `PRODUCT_REVIEW` | `WAITING_FOR_PRODUCT_REVIEW` |
| `PLANNING_COMPLETE` | `APPROVED_FOR_IMPLEMENTATION` |

迁移没有批量改写任何现有项目。具体旧项目只有在读取其唯一根 `project.yaml` 并获得用户授权后才能迁移。

## 5. schema v3 字段变更

### 5.1 Intake 字段

- `active_module`
- `requirements_status`
- `requirements_version`
- `active_requirements`
- `active_interview`
- `intake_round`

这些字段只由 First-Ask Intake 写入。Planner 对 `memory/requirements/` 只读。

### 5.2 产品方案与批准字段

- `proposal_status`
- `proposal_version`
- `active_proposal`
- `approved_proposal`
- `user_approval_status`
- `product_approval_record`
- `active_plan`

只有用户对当前完整方案作出无歧义批准后，Planner 才能设置批准字段并创建正式计划。

### 5.3 Design Exploration 字段

- `design_exploration_required`
- `design_review_status`
- `design_preview_round`
- `active_design_preview_round`
- `selected_design_concept`
- `design_selection_record`
- `design_skip_record`

设计方向选择不等于产品批准。Planner 必须生成新的完整产品方案版本，再等待明确确认。

### 5.4 兼容字段

以下旧字段暂时保留，但新项目不得把它们作为唯一状态来源：

- `product_approval_status`
- `preview_round`
- `active_preview_round`
- `requirement_source`

## 6. 权限与来源链验证

### 6.1 唯一写入者

| 数据 | 唯一写入者 |
| --- | --- |
| 需求采访、需求快照、`requirements_*` | First-Ask Intake |
| 产品方案、设计选择、产品批准、正式计划 | Planner |
| 产品代码和实现证据 | Generator |
| 验收报告、`current_iteration`、`last_evaluation` | Evaluator |

`config/role_policies.yaml` 明确规定 `write_prohibited` 高于普通 `writes`，从而收紧旧配置中 Planner 对需求目录的写权限。

### 6.2 Generator 开始条件

Generator 必须同时验证：

- `status: APPROVED_FOR_IMPLEMENTATION`
- `user_approval_status: approved`
- `active_requirements` 存在
- `approved_proposal` 存在
- `product_approval_record` 存在
- `active_plan` 存在
- 正式计划引用的需求、方案、设计选择和批准记录与根状态一致

任一条件不满足时不得写入 `code/`。

## 7. F7 测试覆盖与证据

归档测试项目：

`C:\Users\28388\Desktop\ai-projects\archive\test_first_ask_integration`

核心报告：

- `TEST_REPORT.md`
- `artifacts/validation/F7_VALIDATION_SUMMARY.md`

| ID | 场景 | 结果 |
| --- | --- | --- |
| T01 | 信息不足时每轮只问 1～3 个高价值问题 | PASS |
| T02 | 已回答或视觉 `undecided` 的问题不重复询问 | PASS |
| T03 | 视觉 `undecided` 路由到 Design Exploration | PASS |
| T04 | 基础事实冲突阻止进入 Planner | PASS |
| T05 | 用户可融合多个设计方向并生成新方案版本 | PASS |
| T06 | “看起来不错”等模糊表述不能批准开发 | PASS |
| T07 | 明确批准后才创建批准记录和正式计划 | PASS |
| T08 | `active_plan: null` 时 Generator 门禁失败 | PASS |
| T09 | 完整来源链允许进入 `IMPLEMENTING` | PASS |
| T10 | 缺少可复现实现证据时 Evaluator 不得 PASS | PASS |
| T11 | `current_iteration: 5` 时停止自动返工 | PASS |

自动结构校验结果：

- 13 个 YAML 文件均可解析。
- 只有一个根级 `project.yaml`。
- 不存在 `memory/project.yaml`。
- 活动来源链缺失文件数为 0。
- 三套预览均包含 `concept.md`、`preview.html` 和 `preview.css`。
- T01–T11 证据为 11/11。
- 测试项目没有写入 Skill 目录。

T10 中虚拟产品的 Evaluator 结论是预期 `FAIL`，因为没有实现代码与验证证据；这证明“无证据不得 PASS”，因此 T10 协议场景本身为 PASS。

## 8. 已知限制

1. 当前环境不可发现 `joyride_request_human_input`。测试使用普通会话适配路径，没有伪造 Joyride 调用。
2. 未验证 Joyride 扩展本身的运行时交互，只验证了输入适配协议和后备路径。
3. F7 验证工作流门禁，没有实现真实报表产品。
4. 三套设计预览通过结构、内容字段和 CSS 引用检查，没有执行浏览器截图级视觉验收。
5. 工作目录不是 Git 仓库，迁移没有提交级回滚点。
6. 已安装 AI Development Team Skill 仍是迁移前版本；工作副本验证通过不等于安装副本已经发布。

## 9. 回滚说明

### 9.1 当前未发布状态

当前最安全的回滚锚点是已安装副本：

`C:\Users\28388\.codex\skills\ai-development-team-skill`

它尚未被本次 First-Ask 迁移覆盖。若用户决定不发布，保持已安装副本不变即可；工作副本中的迁移文件也不会影响 Codex 当前加载的安装版本。

如需把工作副本恢复到安装基线：

1. 用已安装副本恢复 14 个同路径但内容不同的文件。
2. 处理 9 个仅存在于工作副本的新增文件。
3. 任何删除新增文件的操作必须另行获得用户明确同意。
4. 恢复后重新执行 YAML、状态和角色数量检查。

### 9.2 后续发布后的回滚

同步已安装 Skill 前应先创建完整备份目录并记录文件哈希。若发布后发现问题：

1. 停止启动新项目。
2. 从备份整体恢复安装目录。
3. 验证 `SKILL.md`、工作流、角色权限和项目模板哈希。
4. 不自动降级已经使用 schema v3 的具体项目。
5. 对具体项目逐一读取根 `project.yaml`，经用户授权后制定状态回迁方案。

## 10. 测试归档

测试项目已整体移动到：

`C:\Users\28388\Desktop\ai-projects\archive\test_first_ask_integration`

归档保留：

- 唯一根 `project.yaml`
- `TEST_PLAN.md`
- `TEST_REPORT.md`
- 原始请求、采访和需求快照
- 产品方案、设计选择、批准与正式计划
- 三套设计预览
- T01–T11 验证证据
- Evaluator 报告

没有删除测试数据或报告。

## 11. 发布状态与最终门禁

| 检查项 | 状态 |
| --- | --- |
| 工作副本迁移 | 已实施 |
| 工作副本静态验证 | 通过 |
| 隔离协议测试 | 11/11 通过 |
| 测试项目归档 | 完成 |
| 已安装 Skill 同步 | 尚未执行 |
| 用户最终审核 | 等待 |
| 迁移完成声明 | 尚未宣告 |

F8 输出已经具备最终审核条件。用户确认本报告后，才可以：

1. 宣告工作副本迁移完成。
2. 按用户授权决定是否备份并同步到已安装 Codex Skill。

