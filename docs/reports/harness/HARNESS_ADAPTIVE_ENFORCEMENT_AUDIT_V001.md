# Harness Adaptive Enforcement Audit V001

## 审计范围

本轮只审计当前 Skill 仓库，不读取或创建 managed project。检查了 Browser Profile/Policy/Harness、Candidate、Context/Rollover、Completeness、Orchestrator、CLI、Contract、Generator/Evaluator Prompt 及相关测试，并先检查了 Git 基线。

## 基线结论

- 分支：`codex/f10-runtime-correctness-hardening`。
- 初始工作区无用户未提交改动。
- 基线测试：`581 passed, 5 skipped, 113 subtests passed`。
- 既有 F9–F13、CAS、Lease、Recovery、Capability、Secret Boundary 和三角色约束已接入 Runtime 或确定性脚本。

## 发现分类

### 已正式接入 Runtime

- F10 Durable Session、Lease、CAS、Recovery 与 Context Builder。
- Browser Policy/Capability/Broker/Harness 的同源和安全边界。
- F9 Evidence、Issue、Retry、Candidate 旧版选择与恢复建议。

### 仅有工具但依赖 Prompt/调用者自觉

- Browser Gate 没有结构化场景 Manifest 和 Requirement/AC 完整映射约束。
- Candidate 缺少 Profile、Rubric、Evaluator Model、Calibration 和 Required Gate 绑定。
- Contract 有确定性创建/校验脚本，但没有 Generator Runtime Preflight 闭环。
- Evaluator 的多步流水线没有由 Runtime 统一验证执行者和顺序。

### 仅有单元测试或历史确定性 Calibration

- 既有 12 个 Calibration case 是确定性回归校准，不是独立 blind QA 能力证明。
- Context Rollover 已有 Runtime 测试，但阈值固定，没有模型能力/风险适配。
- Candidate 选择有测试，但不区分 Web Profile 的 Browser 必需性。

### 当前验证漏洞

- `browser_validation.required: true` 时 `required_scenarios: []` 可以被接受。
- Browser Gate 只要存在非 `start/close` 步骤就可能通过，不能排除纯 navigate/screenshot。
- Web Candidate 可以使用 `browser_acceptance: SKIPPED`。
- 模型可以返回生命周期字段，缺少 Runtime 层的拒绝点。

### 本轮不处理范围

- 不删除或迁移既有历史工件。
- 不创建第四个 Agent，不改变 Planner/Generator/Evaluator 职责。
- 不改变验收分数、Security、Capability、路径隔离、CAS、Lease、Recovery 或 `current_iteration` 上限。
- 不在 Skill 仓库内创建真实 Calibration/Benchmark 项目数据。

## 阶段审计冲突

未发现需要中止并扩大范围的重大冲突。Docker daemon 不可用已按 `DEFERRED/BLOCKED` 处理，未伪造 PASS。
