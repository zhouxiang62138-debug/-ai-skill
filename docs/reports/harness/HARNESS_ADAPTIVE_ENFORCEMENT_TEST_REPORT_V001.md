# Harness Adaptive Enforcement Test Report V001

## 测试命令与真实结果

| 命令 | 结果 |
|---|---|
| `python -m pytest -q tests/test_browser_harness.py tests/test_best_candidate.py` | 10 passed |
| `python -m pytest -q tests/test_orchestrator_role_selection.py tests/test_context_rollover.py tests/test_event_sequence.py tests/test_recovery_is_idempotent.py` | 11 passed |
| `python -m pytest -q tests/test_evaluator_calibration.py tests/test_implementation_contract.py tests/test_harness_adaptive.py tests/test_candidate_profile_binding.py` | 13 passed, 9 subtests passed |
| `python -m pytest -q tests/test_phase_runner.py tests/test_benchmark_runner.py tests/test_harness_adaptive.py` | 9 passed |
| `python -m pytest -q tests/test_context_rollover.py` | 5 passed |
| `python -m pytest -q` | **592 passed, 5 skipped, 113 subtests passed** |
| `git diff --check` | 通过 |

## 覆盖的强制场景

- Browser Profile 空场景拒绝。
- 必需 Browser 场景缺失、没有真实业务动作或 AC 映射缺失时拒绝 PASS。
- Browser 环境不可用为 BLOCKED，不转成实现 PASS。
- Web Candidate 使用 `SKIPPED` 被拒绝。
- Candidate Profile hash 不一致时禁止恢复。
- PhaseRunner 缺少必需步骤、模型返回 `next_role`、Evaluator 缺少 Gate verifier 时拒绝。
- Model Invocation 成功/失败终态、Role Run 失败与重复调用幂等。
- 未知模型/风险/证据不足选择 FULL；LEAN 仍保留 mandatory gates。
- Blind Calibration 预分类字段拒绝。
- Benchmark 非 `test_` 项目和疑似 Secret 拒绝。

## Docker 状态

Docker 测试实际结果为 `1 passed, 5 skipped`。5 个 skip 均明确标记：`Docker daemon unavailable`，连接 `dockerDesktopLinuxEngine` named pipe 失败。Docker 能力为 `DEFERRED/BLOCKED`，不是 PASS。

## 证据边界

本仓库有真实 Runtime E2E/回归测试，但没有运行真实外部模型、真实浏览器应用项目或真实 Benchmark 数据库；因此不能声称本轮已经证明生产模型质量、真实 Browser 业务覆盖率或成本收益。真实 Calibration/Benchmark 数据应放在仓库外受控 `test_` 项目，并在完成后归档保留 `TEST_REPORT.md`。
