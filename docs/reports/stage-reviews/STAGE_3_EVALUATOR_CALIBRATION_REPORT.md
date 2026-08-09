# Stage 3 — Evaluator Calibration Benchmark

日期：2026-08-09

## 结论

`STAGE RESULT: PASS`

## Implemented

- 新增 `scripts/evaluator_calibration.py`，按 `case-*.yaml` / `expected-*.yaml`
  加载固定 Calibration Case，预测时不读取 expected。
- 新增 `tests/evaluator_calibration/cases/`，覆盖正确 PASS、空按钮、刷新丢失、API
  未连接、核心 placeholder、必需流程失败、console critical error、regression、
  requirement mismatch、fake success UI、Generator 声称 FIXED 但 Bug 仍在和环境阻塞。
- 生成并统计 PASS false positive、FAIL false negative、Critical Bug miss、路由准确率
  和严重度一致率。

## Tests

- Stage 3 定向及相关 Feature/Browser/Evidence：`57 passed, 7 subtests passed`
- 12 个 Calibration Case 全部匹配人工预期。
- PASS false positive rate：`0`
- FAIL false negative rate：`0`
- Critical bug miss rate：`0`
- Correct routing rate：`1.0`
- Severity agreement rate：`1.0`

## Regression

- Calibration 只新增测试工具和测试数据，没有新增 Agent，也没有把 expected 结果写入
  Evaluator 判断路径。
- Gate / Evidence / Feature / Browser 的既有定向测试仍通过。

## Known limitations

- 当前 Calibration 是确定性证据分类基准，不代替真实模型对自然语言报告的判断；后续
  若真实项目暴露误判，再补充少量 Prompt 反例。
- 真实 Playwright Web App 端到端仍需可用的 Playwright 依赖和运行项目。
- Docker Sandbox 仍为 `DEFERRED`。

## Architecture impact

- Calibration Harness 是测试/评估基础设施，不是第四 Agent，不改变 Planner、Generator、
  Evaluator 的角色边界。
- Critical Bug 错判 PASS 已有硬性回归保护。

## Next stage

- 完成 P0 Final Review：清理审计、检查 Secret/临时产物、运行全量 pytest，并输出最终
  Harness Quality Hardening 报告。
