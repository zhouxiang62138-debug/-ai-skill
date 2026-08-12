# Phase R4 — Reference-Guided Design Exploration Report

## R4 RESULT:

PASS — READY_WITH_LIMITATIONS

R4 已将 `active_reference_synthesis` 接入既有 Design Exploration 流程，并保留 Planner → 三个产品方向 → `concept.md` / `preview.html` / `preview.css` → `WAITING_FOR_DESIGN_REVIEW` 的生命周期。本轮没有新增 Agent、没有新增工作流状态，也没有进入 R5 或 R6。

## Files changed:

本轮主要变更集中在：

- `scripts/exploration.py`
- `runtime/context/builder.py`
- `runtime/context/policy.py`
- `config/context.yaml`
- `config/workflow.yaml`
- `config/reference_analysis.yaml`
- `config/role_policies.yaml`
- `config/schemas/project_v6.schema.json`
- `templates/project.yaml`
- `templates/design_concept.md`
- `templates/design_selection.md`
- `templates/design_feedback.md`
- `prompts/planner_prompt.md`
- `DESIGN_EXPLORATION_WORKFLOW.md`
- `docs/PLANNER_APPROVAL_WORKFLOW.md`
- `tests/test_reference_analysis_r4.py`

R0–R3 的历史报告、Reference Analysis 工件和追加式记录均保留，未删除或覆盖。

## Reference-Guided Exploration Architecture:

- Design Exploration 通过 `active_reference_synthesis` 获取当前有效 synthesis。
- Reference Analysis 仍是模块，不是第四个 Agent；Planner 仍拥有产品方向与设计探索决策权。
- 没有引用时保持原有探索路径，不注入虚假的 Reference 指导。
- 只有存在设计相关 Reference 决策时才进入 Reference-guided 分支；纯技术决策不会触发视觉或布局指导。
- 生成结果仍必须包含三个完整方向及每个方向的 `concept.md`、`preview.html`、`preview.css`。

## Concept Strategy Protocol:

`assign_reference_strategies()` 为三个方向生成确定性的差异化策略：

- inspiration：`strong_inspiration`、`balanced_inspiration`、`original_interpretation`
- adaptation：`faithful_adaptation`、`balanced_adaptation`、`original_interpretation`
- close_recreation：`close_recreation`、`balanced_adaptation`、`original_interpretation`

inspiration 模式明确禁止 `reference_faithful`。三个方向的差异不仅是颜色或标签，还由产品定位、特色功能、主要用户路径、信息架构及布局等结构维度共同校验。

## Reference Mode Handling:

支持 `inspiration`、`adaptation`、`close_recreation` 和 `none`。模式冲突、未知模式、过期 synthesis 或无法确认当前 active synthesis 时拒绝进入 Reference-guided 路径，不静默猜测。

## Reference Scope Handling:

设计相关域包括：`information_architecture`、`navigation`、`interaction`、`layout`、`visual_style`、`components`、`design_tokens`、`motion`。技术架构、性能、部署等纯技术决策会被排除在视觉指导之外；显式 `exclude`、`avoid` 和用户排除项优先级最高。

## Design-Relevant Decision Selection:

`select_design_reference_decisions()` 只选择当前 synthesis 中有效、未被排除且属于设计相关域的 `REFDEC-*`。输出保留 synthesis ID、source finding、原始决策类别和显式排除信息，供 Planner 和后续概念追溯使用。

## Concept Traceability:

每个概念的 Reference Integration 元数据包含：

- `reference_synthesis_id`
- `reference_strategy` 与策略变体
- `referenced_decisions`
- `adopted_decisions`
- `adapted_decisions`
- `not_used_decisions`
- `explicit_exclusions`
- `original_design_decisions`

校验器会检查 REFDEC 是否来自当前 active synthesis，禁止引用未知决策、已排除决策或过期 synthesis，并要求概念声明自己的原创设计决策。

## Concept Difference Validation:

既有三方向结构校验继续有效；Reference-guided 概念还必须声明策略和路线差异。仅修改颜色、标题或策略标签而没有产品路径、信息架构、功能或布局差异的方向会被拒绝。

## Preview Integration:

预览文件继续使用原有三文件结构。Reference-guided 预览必须在 HTML 中保留概念 ID、synthesis ID 和策略标记，并在 CSS 中保留 `reference-strategy` / `reference_strategy` 钩子；这些标记用于验证绑定关系，不把原始 URL、图片、HTML 或长篇分析直接注入 Planner Context。

## Design Selection:

设计反馈和选择继续遵守追加式历史规则。设计方向选择不等于产品方案批准；Planner 仍需把选择整合进新的完整产品方案并等待用户再次确认，之后才能生成正式 Plan。

## Product Proposal Revision:

R3 已建立的 Product Proposal Reference Integration 和来源链继续有效。本轮没有把未选中的预览或原始 Reference 数据变成 Generator 的执行输入；正式实施仍只能使用已批准的产品方案、产品规格、Plan 及批准记录。

## Context:

Planner Context 新增受控的 `design_reference_subset` 来源，仅提供 synthesis ID、设计相关决策、决策 ID 和显式排除项。Context Policy 允许该来源，但仍禁止把 raw URL、原始 HTML、图片二进制或完整历史 Finding 注入 Planner。

## Ownership:

- First-Ask：收集事实、目标、约束和 Reference 请求。
- Reference Analysis Module：注册、证据、分析、Finding 和 synthesis。
- Planner：读取 active synthesis，选择并整合设计路线，生成 Design Exploration 工件。
- Generator：只读取 approved plan 及其完整来源链，不读取未批准的预览或 synthesis 原始历史。
- Evaluator：继续独立执行验收，不修改代码、计划或评分标准。

本轮没有创建或假设 Architect、Tester、Security Reviewer、Release Manager 或 Project Manager Agent。

## Backward Compatibility:

- 无 Reference 时的既有 Design Exploration 测试路径保持可用。
- 技术-only synthesis 不会误触发 Reference-guided 视觉路径。
- 既有预览目录、三文件契约和等待设计审核状态保持不变。
- `active_design_reference_synthesis` 在项目 schema 与模板中为可选字段，旧项目不会因缺失该字段而自动升级或改变状态。

## Tests:

- `python -m pytest -q tests/test_reference_analysis_r4.py`：9 passed。
- `python -m pytest -q tests/test_reference_analysis_r4.py tests/test_exploration.py`：31 passed。
- R4 测试覆盖：无引用兼容、技术-only 忽略、显式排除、过期 synthesis 拒绝、确定性策略、概念追溯、未知 REFDEC 拒绝、预览三文件绑定和受控 Context。
- `scripts/exploration.py`、`runtime/context/builder.py`、`runtime/context/policy.py` 与 R4 测试 AST/导入检查通过。
- Context 配置加载检查通过。
- `git diff --check` 通过。

## Regression:

本轮按用户明确授权执行 R4 定向测试。用户本轮没有明确授权完整 R0–R4 回归；尝试合并执行更大范围治理回归时被安全审批器拒绝，因此本报告不宣称完整回归已通过。R3 之前报告中记录的既有完整回归证据保持为历史记录，不作为本轮新证据。

## Known Limitations:

- R4 仍不启用浏览器、Vision、PDF、Video、Repository、Figma 或 Web acquisition 适配器。
- 当前设计相关筛选和策略分配是确定性协议与工件校验，不等同于对参考图片或网页进行真实视觉语义理解。
- 完整 R0–R4 pytest 回归未在本轮授权范围内执行。

## R5 Prerequisites:

- 用户明确批准 R4 结果及下一阶段目标。
- 若 R5 处理图片、网页采集、浏览器截图或 Vision，必须先定义版本化 adapter/capability、受控证据格式、网络边界、失败分类和对应回归测试。
- 必须继续保留 R0–R4 的追加式历史工件，并在 R5 前明确新的输入来源和验收阈值。

## Recommendation:

DO NOT START R5

R4 已完成并停留在 `READY_WITH_LIMITATIONS`；等待用户确认后再决定是否进入下一阶段。
