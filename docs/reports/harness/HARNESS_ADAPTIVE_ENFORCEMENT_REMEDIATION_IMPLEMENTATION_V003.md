# Harness Adaptive Enforcement Remediation Implementation V003

## 实施结果

### Runtime 正式入口与 Attestation

- 新增 `runtime/attestation.py`，提供步骤集合 hash、Verifier 结构校验和 Attestation 摘要。
- `runtime/session_store.py` 新增 append-only `phase_attestations` 表、创建/读取 API、幂等提交查询和 Secret 防护。
- `runtime/event_types.py` 新增 `PHASE_ATTESTATION_CREATED`。
- `runtime/orchestrator.py` 新增 `run_phase/execute_role`，正式串起 HarnessPolicy、Context、PhaseRunner、Runtime Verifier、Attestation 和 CAS。
- `commit_step` 现在拒绝无 Attestation、跨 Run、跨 Invocation/Context、错误 revision、篡改摘要和失败 Verifier；重复提交仍走幂等记录。
- CLI 明确 `run-phase` 只允许宿主注入 ModelInvocationAdapter 后调用 Orchestrator API，不接受任意 Callable。

### Required Steps 与 Verifier Registry

- `runtime/phase_runner.py` 将 Runtime 步骤作为下限，拒绝缩减、重复、空值和未知步骤。
- 模型自报生命周期字段与 `cas_commit` 均拒绝；Transition Intent 由 Runtime 加入 Attestation 后再提交。
- 新增 `runtime/verifiers.py`，内置 Planner/Generator/Evaluator 结构化 Verifier；测试中的 fake verifier 需要显式 test-only。
- Generator `tests` 必须有 Execution Evidence，交接必须有真实存在的文件引用。

### Generator Contract Preflight

- `PhaseRunner` 从当前项目的批准来源读取计划、规格、需求和 Plan Approval，拒绝调用者传入的另一套来源。
- `feature=None` 和无法确定风险的来源均 fail closed。
- `classify_contract_need` 增加 `policy_path` 绑定，和 `validate_contract_history` 使用同一策略。
- Change Request 的确定性 Role Transition 通过真实 Runtime Transition Verifier、Invocation 和 Attestation 后进入严格 `commit_step`。

### Candidate 与 Browser

- `scripts/best_candidate.py` 拒绝新增 Candidate v1；v1 恢复需显式 read-only，v2 需要完整当前 Profile 绑定和 Required Gate 覆盖。
- `CandidateRuntimeService` 的 Profile-bound best 只选 v2，v2 恢复要求 Evaluator Model。
- Browser Profile 支持 Manifest Hash；新增 `load_scenario_manifest`，执行项目内路径、symlink、模板/示例和 required scenario 校验。
- Browser Gate 增加 Scenario Hash、Requirement/AC、步骤顺序、UI 观察和 API 状态证据检查；通用 `web_app` 场景标记为 template。

### Blind Calibration 与 Benchmark

- `scripts/evaluator_calibration.py` 新增递归 allowlist、独立 `BlindEvaluatorAdapter` 和 `BLOCKED/UNAVAILABLE` 缺省结果；旧 12 Case 仍保留为 deterministic routing calibration。
- `scripts/benchmark_runner.py` 增加有限数值、区间、严格 bool、整数和受限 Execution/Capability Context 校验；结果使用 O_EXCL 追加写入。
- 对应 Calibration、Browser、Candidate、PhaseRunner、Harness Adaptive、Benchmark 和完整回归测试均扩展。

## 兼容与边界

- V001/V002 报告与历史 Candidate v1 文件语义不变；旧测试只读 fixture 不再调用生产 v1 追加接口。
- Docker、真实外部 Model、真实 web app、Playwright 业务流、人工复核和外部 Benchmark 仍需在仓库外 `test_` 项目验证。

## 最终收紧补充

- 正式 `PhaseRunner` 现在必须使用 Runtime-owned Verifier Registry；任意自定义 verifier 只有在显式 `test_only` 测试路径才可注入，避免生产入口通过替换 verifier 放宽门槛。
- Candidate v2 比较增加 cohort 一致性检查；不同 Profile、Profile hash、rubric、calibration 或 evaluator 绑定不能混合比较；可选 workspace hash 在恢复时也会复核。
