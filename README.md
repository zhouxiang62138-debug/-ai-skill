# AI Development Team Skill

这是 AI Development Team 的 Skill 本体仓库，提供 First-Ask Intake、Planner、
Generator、Evaluator 协作协议，以及配套的 Runtime、配置、模板、脚本和测试。

本仓库不是 managed project，不需要也不应创建根目录 `project.yaml`。具体项目位于
`C:\Users\28388\Desktop\ai-projects\<project_id>`，项目数据、代码和运行工件不得写入
本仓库。

## 主要入口

- `SKILL.md`：Skill 使用方式和能力边界。
- `AGENTS.md`：仓库级协作、审批和维护规则。
- `intake/first_ask.md`：First-Ask Intake Module。
- `prompts/`：Planner、Generator、Evaluator 的角色提示词。
- `runtime/`：正式运行时实现。
- `config/`：工作流、策略、评估规则和 Schema。
- `templates/`：managed project 工件模板。
- `scripts/`：确定性维护和协议执行工具。
- `tests/`：Runtime、工作流和迁移测试。
- `docs/`：当前协议与架构文档。
- `docs/reports/`：Skill 本体开发过程中的历史审计、设计和验收报告。
- `experimental/`：仅供追溯和兼容测试的历史原型，不是正式 Runtime 调用路径。

## 开发检查

```powershell
python -m pytest
git diff --check
```

历史报告的分类和读取规则见 `docs/reports/README.md`。当前正式能力状态以
`docs/RUNTIME_CAPABILITY_STATUS.md` 为准。
