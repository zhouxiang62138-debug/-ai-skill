# Harness Adaptive Enforcement Design V001

## 设计原则

Harness 是 Runtime 可验证的执行闭环，不是 Prompt 的额外说明；Orchestrator、PhaseRunner、Context Builder、Contract Preflight、Benchmark Runner 都是 Module/确定性基础设施，不是 Agent。核心角色仍只有 Planner、Generator、Evaluator。

## 闭环

1. Orchestrator 提供合法 Role Run、Lease 和项目状态。
2. PhaseRunner 构建受 Role/状态约束的 Context，并创建 durable Model Invocation。
3. Generator 先通过 approved source chain；高风险特征再通过 Contract Preflight。
4. Evaluator 必须为每个必需阶段提供 Runtime Gate verifier；模型文字声明不算证据。
5. 所有必需步骤完成后，才允许通过既有 CAS 提交；失败只记录结构化原因，不提交候选业务状态。
6. 重复调用读取已持久化 Role Run 结果，不重复创建 Invocation、Event 或业务工件。

## 新结构

- `BrowserScenarioManifest v1`：场景 ID、Requirement、AC、前置条件、操作步骤、UI/API 预期、关键工作流和场景类型。
- `Candidate v2`：Profile、Profile hash、Rubric version、Evaluator model、Calibration suite version、Required Gate results。
- `Harness Policy v1`：模型能力、复杂度、风险、Browser、历史 PASS/返工和证据充分性输入；未知输入默认 FULL。
- `Calibration Observation v1`：只允许原始观察，最终 PASS/FAIL、分类、严重度和路由由独立人工复核保存。
- `Benchmark Result v1`：受控变体的耗时、Token、成本、工具调用、压缩、Evaluator 轮次、覆盖率、误报和最佳 Candidate 指标。

## 兼容策略

- Candidate v1 保留为 legacy read-only；只有 Candidate v2 执行 Profile-bound Gate 强制校验。
- 旧 Context rollover 配置只读取 `RolloverPolicy` 已声明字段，额外模型化字段由 Harness Policy 使用。
- Browser Profile 仍支持显式 `required_scenarios`；也支持合法 `scenario_manifest.reference`。
- 不回写、不覆盖既有 Candidate、Calibration、Evaluation、Plan 或报告。

## 安全与职责不变量

- Runtime 不接受模型返回的 `next_role`。
- Generator 不直接写 `project.yaml`；状态提交仍走 Lease + expected revision + CAS。
- Evaluator 不写 `code/`，Planner 不写 `code/`，三角色边界不变。
- Event、Context、Candidate、Evidence、Benchmark 只保存受控引用、摘要或 hash，不保存凭据。
- Web Profile 的 Browser `SKIPPED` 不合法；Browser 环境不可用只能 BLOCKED。
