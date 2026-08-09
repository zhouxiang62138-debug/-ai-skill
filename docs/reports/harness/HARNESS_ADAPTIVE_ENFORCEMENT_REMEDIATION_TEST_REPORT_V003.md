# Harness Adaptive Enforcement Remediation Test Report V003

本报告记录 V003 修复后的最终验证结果。V001、V002 报告继续保留为历史审计记录，本报告不覆盖它们。

## 最终命令与结果

- `python -m pytest -q`：**608 passed, 5 skipped, 113 subtests passed in 77.48s**。
- `git diff --check`：通过；没有 whitespace error，仅有 Git 关于工作区 LF/CRLF 转换的提示。
- Docker 定向测试：仍有 5 个 skip，原因是 Docker daemon 不可用，无法连接 Docker Desktop Linux engine named pipe；这些结果保持为 `DEFERRED/BLOCKED`，不计为 PASS。

## 反规避测试覆盖

- 正式 Orchestrator → HarnessPolicy → PhaseRunner → Runtime Verifier → Runtime Attestation → `commit_step` → CAS 链路。
- `commit_step` 缺失、跨 Run/Invocation/Context、revision 不一致、Attestation 篡改或 Verifier 失败均拒绝。
- `required_steps` 缩减、重复、空步骤和未知步骤拒绝；模型不能提交 `next_role`、`active_module`、`cas_commit` 等 Runtime 字段。
- Generator 的 Plan、Product Spec、Requirements、Plan Approval 只能从当前 `project.yaml` 来源链读取；无法判定风险时 Contract fail closed。
- Candidate v1 生产追加拒绝；v2 要求 Profile、hash、rubric、calibration、Evaluator Model 和 Required Gates 绑定。
- Browser 要求项目内 Scenario Manifest、Manifest hash、Requirement/AC 映射、精确动作序列、UI observation 和 API state evidence；模板/示例 Manifest 不能作为验收证据。
- Blind Calibration 对原始 observation 使用递归 allowlist；没有外部 Evaluator Adapter 时只能返回 `BLOCKED/UNAVAILABLE`。
- Benchmark 拒绝 bool、NaN、Infinity、负数、越界 ratio/score，以及不合规的输出路径和无能力的执行入口。

## 证据边界

上述测试证明 Runtime 的强制边界和 fail-closed 行为；它们不等价于真实外部模型质量、真实 web app、Playwright 业务覆盖率、外部 Evaluator 校准质量或 Docker 环境验收。后续真实验证仍需在仓库外、使用 `test_` 前缀的 managed project 中执行并保留 `TEST_REPORT.md`。
