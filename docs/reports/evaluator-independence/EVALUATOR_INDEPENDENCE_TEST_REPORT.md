# E1 Evaluator Independence Hardening 测试报告

## 测试范围

测试覆盖 Fresh Invocation、独立 Context、Generator 声明降级、证据来源、revision/代码快照绑定、浏览器复现失败、Tool Store 反查、Evaluator 写边界、证据 Manifest 和既有 Stage 2/3/4B 流程。

## 已执行结果

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/test_evaluator_independence.py -q` | 13 passed |
| `python -m pytest tests/test_stage2_core_e2e.py tests/test_stage3_rework_e2e.py tests/test_stage4b_change_request_e2e.py -q` | 15 passed |
| `python -m pytest tests/test_phase_runner.py tests/test_context_builder.py tests/test_evaluation_evidence.py -q` | 54 passed，7 subtests passed |
| 核心回归（排除外部 reference / browser 依赖） | 623 passed，5 skipped，113 subtests passed |

## 关键回归断言

- Generator 的 `GENERATOR_PROVIDED` 证据不能支撑关键 PASS。
- 旧 revision、旧代码快照、失败的 browser reproduction 和缺失 E1 payload 都会被拒绝。
- 正式 Verifier 能反查 Tool Call / Tool Attempt，且结果哈希不一致会拒绝验收。
- durable Context Manifest 会保存 `context_type` 和排除源，旧数据库迁移后仍可恢复 E1 隔离证明。
- Change Request 的 handoff 仍可被 Evaluator 作为事实输入，但 Generator 私有响应路径不会进入盲审上下文。
- 原有 Planner、Generator、Evaluator 三角色不变，没有引入第四个 Agent。

## 环境说明

本机默认临时目录受沙箱限制，pytest 需要系统临时目录写权限；受控升级权限后测试正常运行。全量 pytest 曾在受限临时目录环境中超时并出现 pytest 捕获输出异常，因此本报告只列出已实际完成且有明确退出码的分组结果，不把未完成的全量运行写成 PASS。
