# Harness Adaptive Enforcement Remediation Design V003

## 目标

本轮只堵住 V001/V002 之后已实测成立的 Harness 绕过，不扩展产品功能。设计保持 Planner、Generator、Evaluator 三个 Agent；PhaseRunner、HarnessPolicy、Contract Preflight、Browser Loader、Calibration Runner 和 Benchmark Runner 均为 Runtime Module/确定性基础设施。

## 运行闭环

正式入口为 `Orchestrator.execute_role/run_phase`：

1. Orchestrator 创建 Role Run，并调用 HarnessPolicy。
2. Runtime 构建 Context，创建 durable Model Invocation。
3. PhaseRunner 合并 Runtime 默认步骤与合法附加步骤，拒绝缩减、重复和未知步骤。
4. Runtime-owned Verifier Registry 对来源、Contract、实现范围、真实执行证据、Browser/Evaluator 证据和交接文件执行确定性校验。
5. Runtime 追加 Phase Attestation，绑定 Session、Run、Role、Revision、Context、Invocation、步骤 hash、Verifier 结果、Evidence 引用和幂等键。
6. `commit_step` 只接受已持久化且完整校验的 Attestation ID，然后继续走现有 Lease、Fencing、expected_revision 和 CAS。

旧 E2E 测试通过测试夹具真实创建 Attestation；正式 API 不提供可由调用者控制的 v1 或无证明放行开关。

## 各阶段方案

### Required Steps 与 Verifier

- Runtime 默认步骤集合是下限；调用者只能追加。
- 模型 `completed_steps` 只是声明，不能替代 Verifier 结果。
- Runtime 拒绝模型返回 `cas_commit`、`next_role`、`active_module`、`runtime` 和 `schema_version`。
- Formal Orchestrator 只接收 Runtime Verifier Registry；任意 lambda 只能在显式 test-only 单元路径使用。

### Contract 来源

Generator 只从当前项目 `project.yaml` 指向的批准计划、产品规格、需求和 Plan Approval 记录读取来源。风险事实必须从受保护的 Feature Manifest 推导；来源缺失、调用者替换来源或风险无法确定时 fail closed。分类和历史校验使用同一个 `policy_path`。

### Candidate

- Candidate v1 保留为历史 read-only 输入，生产追加接口拒绝新建 v1。
- Candidate v2 恢复必须提供当前 Profile、Profile Hash、Rubric 和 Calibration 绑定；Runtime 服务还要求 Evaluator Model 绑定。
- Required Gate 集合从 Profile/Policy 推导，所有 Gate 必须存在且 PASS；Profile-bound 最佳 Candidate 不比较 v1 或不同规则 Cohort。

### Browser Manifest

Profile 声明 `scenario_manifest` 后由 Runtime 按项目根目录 Path Policy 加载，拒绝绝对路径、父目录和 symlink/junction 逃逸。通用 Skill 场景标记为 `template`，正式 Gate 拒绝直接使用。Gate 检查 Scenario、Requirement/AC、操作顺序、业务动作、UI 观察和 API/Runtime/DB 状态证据。

### Blind Calibration 与 Benchmark

- 新 Blind Observation 使用递归 allowlist；所有 Schema 层级关闭额外属性，Expected/Adjudication 与观察输入分离。
- 没有独立 Evaluator Adapter 时返回 `BLOCKED/UNAVAILABLE`，不报告已校准或零漏检。
- Benchmark 严格限制比率 0～1、分数 0～10、有限数值、非 bool 数字、严格 bool、Skill 外输出目录、`test_` 项目和受限 Capability Context。

## 不变约束

CAS、Lease、Fencing、Recovery、Capability、Path、Secret、Evidence、Append-only、三 Agent 边界、`current_iteration == 5` 停止规则和 Docker `DEFERRED/BLOCKED` 语义均不降低；不创建 managed project、不保存真实项目数据、不 commit/push/建 PR。
