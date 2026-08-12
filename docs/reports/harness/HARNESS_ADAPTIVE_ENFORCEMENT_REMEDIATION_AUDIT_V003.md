# Harness Adaptive Enforcement Remediation Audit V003

## 审计范围

本轮只审计并修复当前 Skill 本体仓库中的 Harness Runtime 绕过，不读取或创建任何 managed project，也不创建假的 `project.yaml`。V001/V002 报告与现有未提交修改均保留。

## 已确认漏洞

1. `PhaseRunner.run(required_steps=...)` 直接把调用者传入的步骤作为最终集合，调用者可以缩减 Runtime 默认步骤。
2. `PhaseRunner` 的 Gate verifier 允许由调用者注入任意 Callable，且模型的 `completed_steps` 仍是主要完成依据。
3. `Orchestrator.commit_step` 没有要求 Runtime 创建的 Phase Attestation，原始提交入口可以绕过 PhaseRunner。
4. Generator Contract Preflight 信任调用者传入的 `feature`、批准计划和 Requirement/AC；`feature=None` 可跳过高风险检查。
5. Candidate v1 在 `append_candidate` 中仍可新增；Candidate v2 恢复的 Profile、Rubric、Calibration 和 Gate 绑定参数可省略。
6. Browser Gate 在缺少 Scenario Manifest 或 Requirement/AC 映射错误时仍可能根据任意业务动作通过。
7. Blind Calibration 使用递归 denylist，而不是所有层级 `additionalProperties: false` 的 allowlist Schema，并直接复用带预分类语义的旧预测逻辑。
8. Benchmark Runner 对比率、分数、NaN/Infinity、严格 bool、序列化类型、并发编号和执行上下文的约束不足。

## 根因

- Runtime 的事实、证明和模型声明没有清晰分层。
- 正式入口没有统一串起 Context、Invocation、Verifier、Attestation 和 CAS。
- 部分旧兼容 API 的关键参数仍然是可选的，缺失值被当成“未指定”而不是拒绝。
- Browser、Calibration 和 Benchmark 的结构校验没有在运行时实际执行完整 Schema/来源约束。

## 影响范围

影响 Planner、Generator、Evaluator 的正式执行、提交前证明、Contract 来源链、Candidate 恢复、Browser 验收、Blind Calibration 和 Benchmark 结果可信度；不改变三 Agent 约束、CAS、Lease、Fencing、Recovery、Capability、Path、Secret、Evidence 和 Append-only 规则。

## 预计修改文件

- `runtime/phase_runner.py`、`runtime/orchestrator.py`、`runtime/cli.py`
- `runtime/session_store.py`、`runtime/event_types.py`、新增 Runtime Attestation/Verifier 支持
- `runtime/contract_preflight.py`、`scripts/implementation_contract.py`
- `runtime/candidates.py`、`scripts/best_candidate.py`、Candidate 配置与 Schema
- `runtime/browser/models.py`、`runtime/browser/evidence.py` 及 Browser Schema
- `scripts/evaluator_calibration.py` 及 Calibration Schema
- `scripts/benchmark_runner.py` 及 Benchmark Schema
- 对应对抗测试和 V003 设计、实现、测试、最终评估报告

## 兼容方案

- 保留历史 Candidate v1、旧 Calibration Case 和 V001/V002 报告；v1 只允许显式 legacy read-only 读取。
- 旧的直接提交调用若必须保留，显式标记为 `legacy/test-only`；schema v7 正式入口要求 Runtime Attestation。
- 既有 12 个 Calibration Case 继续作为 deterministic routing calibration，不把它们冒充真实 blind QA。
- 现有通用 Browser 场景不删除，只作为 template/example；正式项目必须使用受保护的项目场景 Manifest。

## 本轮明确不处理的范围

- 不创建第四个 Agent，不把 Module 写入 `next_role`。
- 不降低任何 CAS、Lease、Fencing、Recovery、安全能力或验收阈值。
- 不启动真实外部模型、真实 web app、Docker、外部 Evaluator 或人工 Calibration。
- 不在 Skill 仓库保存真实项目测试数据，不 commit、push 或创建 PR。
