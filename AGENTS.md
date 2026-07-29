# AI Development Team 共享规则

## 产品确认规则

- First-Ask 是 `first_ask_intake` Intake Module，不是第四个 Agent，也不得写入 `next_role`。
- First-Ask 只收集事实、目标和约束；需求达到 `sufficient_for_planning` 并设置有效 `active_requirements` 后，才能进入 Planner。
- Planner 必须先读取 `active_requirements`，不得覆盖需求记录，不得换一种说法重复询问已回答事项。
- Planner 必须先生成 `memory/proposals/product_proposal_v<nnn>.md`；需要设计探索时进入 `DESIGN_EXPLORATION`，无需或经用户明确同意跳过时才进入 `WAITING_FOR_PRODUCT_REVIEW`。任何路径都不得跳过用户确认直接生成正式计划。
- 当用户只明确 App 目标、尚未确定风格或布局，或明确希望先看参考方案时，Planner 必须在产品方案草稿后进入 Design Exploration。
- 每轮 Design Exploration 必须在 `artifacts/design_previews/round_<nnn>/` 生成 3 个有实质差异的设计方向；每个方向必须包含 `concept.md`、`preview.html` 和 `preview.css`。
- 设计预览只能作为设计验证工件，Planner 仅可写入 `artifacts/design_previews/`，不得修改 `code/`。
- 用户可以单选、融合、修改或要求新一轮设计方向。设计预览、用户反馈和设计选择记录均为追加式历史工件，禁止覆盖。
- 用户选择设计方向不等于批准产品方案。Planner 必须将选择整合进新的完整产品方案版本，并再次进入 `WAITING_FOR_PRODUCT_REVIEW`。
- 用户未明确选择设计方向且未明确确认整合后的产品方案时，禁止生成正式计划，禁止进入 Generator。
- 只有用户明确同意并留下决策记录时，Planner 才可跳过 Design Exploration；沉默或已有零散风格描述不构成跳过确认。
- 只有用户明确确认当前方案后，Planner 才可生成 `memory/plans/plan-<nnn>.md` 并将工作交给 Generator。
- 产品方案、用户反馈、确认记录和正式计划均为追加式历史工件，禁止覆盖。
- Generator 只能根据 `active_requirements`、`approved_proposal`、`design_selection_record` 或 `design_skip_record`、`product_approval_record` 与 `active_plan` 元数据验证来源；不得读取 `memory/proposals/` 或未选中的设计预览作为实施依据，也不得进入未批准需求的开发。
- 用户拥有最终产品决策权；沉默、模糊表述和“需求很简单”均不构成确认。

当前角色仅限 Planner、Generator、Evaluator。不得创建、假设或代替 Architect、Tester、Security Reviewer、Release Manager 或 Project Manager Agent。

每次开始项目工作，必须先读取项目根目录唯一的 `project.yaml`。不得创建 `memory/project.yaml`，不得用聊天记录替代项目状态。

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
