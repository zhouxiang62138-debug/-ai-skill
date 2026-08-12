# Harness Adaptive Enforcement Remediation Final Evaluation V003

## V003 结论

V003 已修复本轮确认的 Harness Adaptive Enforcement 绕过点，并通过完整本地回归。当前结论为 **READY_WITH_BLOCKERS**：没有发现仍可由模型直接利用的 P0 Runtime 强制绕过，但 Docker、真实外部系统和真实评估数据尚未具备，因此不能宣告生产级全链路 PASS。

## 13 项验收问题

1. **是否存在正式 PhaseRunner 入口？** 是。正式 `Orchestrator.run_phase/execute_role` 串起 HarnessPolicy、Context、Model Invocation、Role Run、PhaseRunner、Runtime Verifier、Attestation 和 CAS；有对应 E2E/adversarial 测试。
2. **是否能绕过 Attestation 直接 commit-step？** 否。严格 `commit_step` 必须使用同一 Run/Invocation/Context、同一 revision 且存在且未篡改的 Runtime Attestation。Change Request 路径也已改为先生成 Runtime Attestation 再提交。
3. **是否能缩减 required_steps？** 否。调用方只能增加步骤，不能删减、重复、传空或传未知步骤；模型返回的完成步骤还必须与 Runtime 集合精确一致。
4. **是否能伪造 model completed_steps/evidence？** 正式 Runtime Verifier Registry 是必需的，测试替身必须显式标记为 test-only；Generator tests 还必须有成功执行证据和真实文件引用。模型不能提交 Runtime 控制字段。
5. **`feature=None` 或高风险 Contract 是否能绕过？** 否。Contract 来源绑定到当前项目批准记录；feature 缺失或风险不可判定时 fail closed，且分类与验证使用同一 policy 路径。
6. **Candidate v2 是否绑定完整当前 Profile？** 是。生产 Runtime Service 只接受 v2，并校验 evaluation profile/hash、rubric、calibration、Evaluator Model、Required Gates，以及可选 workspace hash。
7. **Candidate v1 是否仅只读？** 是。生产追加 v1 被拒绝；历史 v1 只能通过显式 `legacy_read_only` 读取，测试 fixture 不调用生产追加 bypass。
8. **Browser 是否强制 Scenario Manifest？** 是。声明 Manifest 的 Profile 必须加载项目内 Manifest 并校验 hash、schema、required scenarios、映射、动作顺序、UI/API 证据；template/example 工件被拒绝。
9. **错误 Requirement/AC 是否能通过？** 否。Browser Gate 对 Manifest 映射和实际 Candidate 的 Requirement/AC 绑定进行校验，错误映射会拒绝。
10. **Blind Calibration 是否只接收原始 observation？** 新的 Mapping 输入路径只允许递归 allowlist 中的原始 observation 字段；没有真实外部 Evaluator Adapter 时结果为 `BLOCKED/UNAVAILABLE`。历史 12 Case 保留为 deterministic routing calibration，不冒充真实盲评。
11. **Benchmark 数值和路径边界是否严格？** 是。拒绝 bool、NaN、Infinity、负数和越界 ratio/score；执行必须具备正式 capability context，输出目录必须是外部、`test_` 项目范围并使用追加安全写入。
12. **完整测试结果是什么？** `python -m pytest -q`：**608 passed, 5 skipped, 113 subtests passed in 77.48s**；`git diff --check` 通过，仅有 LF/CRLF 提示。
13. **已知限制是什么？** Docker daemon 当前不可用；尚未运行真实外部模型、真实 web app/Playwright、外部 Evaluator Adapter、人工复核或真实 Benchmark 数据。正式宿主仍需注入真实 ModelInvocationAdapter 和外部 Gate/evidence；这些限制不降低 Runtime 的 CAS、Lease、Fencing、Recovery、Capability、Path、Secret 或 Evidence 校验。

## 最终判断

- P0 绕过：本轮确认的路径均已收紧，当前未发现可复现的直接绕过。
- 生产声明：因 Docker 和真实外部证据缺失，保持 `READY_WITH_BLOCKERS`，不宣告 PASS。
- 历史记录：V001/V002 和既有工件均保留；本仓库仍是 Skill 本体仓库，不创建虚假 `project.yaml`，不将测试数据写入 Skill 目录。
