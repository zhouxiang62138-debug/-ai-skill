# 项目约定

每个项目位于 `C:\Users\28388\Desktop\ai-projects\<project_id>`，根目录只能有一个 `project.yaml`。禁止在 `memory/` 创建第二个状态文件。

原始请求、采访和需求快照采用追加编号：`request-001.md`、`interview-001.md`、`requirements_v001.yaml`。项目计划、交接和报告采用追加编号：`plan-001.md`、`handoff-001.md`、`evaluation-001.md`。产品方案采用三位追加版本：`product_proposal_v001.md`、`product_proposal_v002.md`。设计选择记录采用 `design-selection-001.md`，设计预览轮次采用 `artifacts/design_previews/round_001/`。历史工件不得覆盖。项目之间默认隔离，除非用户明确授权复用。

项目中：`code/` 保存生产代码；`memory/requirements/` 由 First-Ask Intake 写入、Planner 只读；`memory/` 的其他目录保存产品方案、计划、交接与决策；`evaluation/` 保存验收报告；`artifacts/` 保存设计预览、测试日志、截图和构建物。Planner 只能在 `artifacts/design_previews/` 创建设计验证工件，不得把预览写入 `code/`。

每个设计预览轮次必须包含 `concept_01`、`concept_02` 和 `concept_03`。每个概念目录包含 `concept.md`、`preview.html` 和 `preview.css`。新反馈需要重新比较方向时，创建新的轮次目录，禁止覆盖旧轮次。
