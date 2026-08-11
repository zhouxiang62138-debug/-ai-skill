# AI Development Team 共享规则

## 产品确认规则

- First-Ask 是 `first_ask_intake` Intake Module，不是第四个 Agent，也不得写入 `next_role`。
- First-Ask 只收集事实、目标和约束；需求达到 `sufficient_for_planning` 并设置有效 `active_requirements` 后，才能进入 Planner。
- Planner 必须先读取 `active_requirements`，不得覆盖需求记录，不得换一种说法重复询问已回答事项。
- Planner 必须先生成 `memory/proposals/product_proposal_v<nnn>.md`；需要设计探索时进入 `DESIGN_EXPLORATION`，无需或经用户明确同意跳过时才进入 `WAITING_FOR_PRODUCT_REVIEW`。任何路径都不得跳过用户确认直接生成正式计划。
- 当用户只明确 App 目标、尚未确定风格或布局，或明确希望先看参考方案时，Planner 必须在产品方案草稿后进入 Design Exploration。
- 新 Design Exploration 默认分两阶段执行。第一阶段 `direction_comparison` 必须在
  `artifacts/design_previews/round_<nnn>/` 生成 3 个有实质差异的轻量产品路线；
  每个方向只要求 `concept.md`，本轮共用 `comparison.html` 与 `comparison.css`。
  差异必须体现在产品定位、特色功能、主要用户路径或信息架构，不能只换色。
- 用户单选、确定修改、混搭或恢复旧方向后，不得直接整合产品方案；必须开启
  新轮次 `selected_prototype`，只在 `selected_concept/` 生成一套完整的
  `concept.md`、`preview.html` 与 `preview.css`，并对这一套执行完整浏览器验证。
  用户明确确认该高保真预览后，才能进入产品方案整合。
- 进入 `WAITING_FOR_DESIGN_REVIEW` 前必须按当前 `design_preview_mode` 通过校验。
  第一阶段只做三方向结构、共用比较页标记和路线差异校验；第二阶段才做完整
  页面、导航、关键功能和浏览器校验。每个阶段每轮最多自动尝试 2 次；中断后
  只补齐缺失工件，不得覆盖已有非空工件。
- 设计预览只能作为设计验证工件，Planner 仅可写入 `artifacts/design_previews/`，不得修改 `code/`。
- 用户可以单选、融合、修改或要求新一轮设计方向。设计预览、用户反馈和设计选择记录均为追加式历史工件，禁止覆盖。
- 每次设计反馈必须先创建 `design-feedback-<nnn>.md`，逐字保存用户原话并
  结构化记录动作。含糊、讨论或冲突反馈不得创建设计选择记录；单选、确定的
  修改、混搭或恢复旧方向必须创建独立的 `design-selection-<nnn>.md`。
- 全部否定或要求查看修改后预览时必须递增预览轮次并保留旧轮次。恢复旧方向
  通过新选择记录引用历史工件，禁止覆盖或回写旧记录。
- 用户选择设计方向不等于批准高保真预览或产品方案。Planner 必须先生成并取得
  用户对单一高保真预览的明确确认，再将其整合进新的完整产品方案版本，并再次
  进入 `WAITING_FOR_PRODUCT_REVIEW`。
- 用户未明确选择设计方向、未明确确认单一高保真预览或未明确确认整合后的产品
  方案时，禁止生成正式计划，禁止进入 Generator。
- 只有用户明确同意并留下决策记录时，Planner 才可跳过 Design Exploration；沉默或已有零散风格描述不构成跳过确认。
- 用户明确确认当前产品方案后，Planner 才可生成正式产品规格和待审核
  `memory/plans/plan-<nnn>.md`，并进入 `WAITING_FOR_PLAN_REVIEW`。产品确认
  不构成 Plan 批准。
- 只有用户再明确批准当前开发 Plan、创建 `plan-approval-<nnn>.md` 且完整
  来源链校验通过后，才能进入 `APPROVED_FOR_IMPLEMENTATION` 并交给 Generator。
- 产品方案、用户反馈、确认记录和正式计划均为追加式历史工件，禁止覆盖。
- Generator 只能根据 `active_requirements`、`approved_proposal`、
  `design_selection_record` 或 `design_skip_record`、`product_approval_record`、
  `active_product_spec`、`approved_plan` 与 `plan_approval_record` 验证来源；
  `approved_plan` 是唯一可执行输入。不得读取 `memory/proposals/` 或未选中的
  设计预览作为实施依据，也不得进入未批准需求的开发。
- 产品或 Plan 批准撤销必须追加记录并使当前执行授权失效，不得删除历史工件。
  Generator 已开始后发生核心方向变更时，必须停止并进入正式变更控制。
- 用户拥有最终产品决策权；沉默、模糊表述和“需求很简单”均不构成确认。

当前角色仅限 Planner、Generator、Evaluator。不得创建、假设或代替 Architect、Tester、Security Reviewer、Release Manager 或 Project Manager Agent。

## Skill 本体仓库与 managed project 边界

本仓库是 `ai-development-team-skill` Skill 本体仓库，不是
`C:\Users\28388\Desktop\ai-projects\<project_id>` managed project。Skill
仓库根目录不要求存在 `project.yaml`，禁止为了满足项目协议创建假的
`project.yaml`。`project.yaml` 只属于具体 managed project 实例。

Skill 本体开发、测试、文档维护、Git commit、Git push 和 GitHub 上传不依赖
`project.yaml`，不得因 Skill 仓库根目录缺少该文件而 BLOCKED。只有 Planner、
Generator、Evaluator、Change Request 等 managed project 工作流才要求读取对应
项目根目录的 `project.yaml`，并遵守其状态协议。

仅在开始 managed project 工作时，必须先读取该项目根目录唯一的 `project.yaml`。
不得创建 `memory/project.yaml`，不得用聊天记录替代项目状态。

## F10 Managed Runtime 规则

- Orchestrator、Session Store、Lease Manager、Recovery 和 Context 构建入口都是确定性基础设施，不是第四个 Agent。
- schema v7 项目的完整 Session/Event 保存在项目 `.runtime/sessions.sqlite3`；`project.yaml.runtime` 只保存当前投影。
- v3、v4、v5、v6 项目首次读取必须保持只读；只有显式检查、预览、备份、迁移、验证流程才能进入 v7。
- schema v7 的 `project.yaml` 写入必须持有有效 Worker Lease，并提供 `expected_revision` 通过 Compare-And-Swap；遗留 writer 不得直接写入。
- 等待用户、阻塞、已验收和归档状态不得启动角色。
- Runtime 恢复必须幂等，不得重复生成业务工件、Event、Checkpoint 或状态递增。
- Event Payload 不得包含凭据或疑似 Secret；长工具输出只保存受控引用和 hash。

- Planner 不得修改 `code/`，不得宣布 PASS 或 FAIL。
- Planner 不得修改 `memory/requirements/`；基础事实缺失、变化或冲突时，只能通过追加式 Intake 交接返回 First-Ask。
- Generator 不得修改评分标准、验收阈值或最终 PASS/FAIL。
- Evaluator 不得修改代码或计划，不得自行改变验收标准。
- 没有可复现的必需验证证据不得 PASS。
- 计划、交接记录和验收报告均为追加式历史工件，禁止覆盖。
- 项目之间默认隔离；没有用户明确授权不得读取其他项目的数据、代码或工件。
- Skill 目录不得保存任何具体项目的需求、计划、代码、报告、测试日志、截图或构建产物。
- `current_iteration` 达到 5 时，必须进入 `WAITING_FOR_USER`，不得继续自动修改。

## Test Project Policy

测试项目必须：

1. 使用 test_ 前缀

例如：

test_todo_app


2. 测试完成后：

进入 archive/


3. 测试数据不得进入 Skill目录


4. 测试报告必须保留：

TEST_REPORT.md
