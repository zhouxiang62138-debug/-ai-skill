# Stage 3 — Evaluator Calibration Benchmark 设计

日期：2026-08-09

## 目标

验证 Evaluator / Gate 组合能否识别真实 Critical Bug，避免“发现问题后自我解释、
降低严重度、错误 PASS”。

## 文件协议

每个 Calibration Case 独立保存：

- `case.yaml`：只包含验收输入、Gate、Browser/Feature 证据和观察到的问题；
- `expected.yaml`：人工固定的结果、Issue Class、最低严重度和路由。

分类器只读取 `case.yaml`；expected 只在统计比较时读取。这样可以防止测试工具把
expected 当作答案直接回传。

## 统计

- PASS false positive rate
- FAIL false negative rate
- Critical bug miss rate
- Correct routing rate
- Severity agreement rate

Critical Bug 被预测为 PASS 时，测试必须失败；当前基准还要求关键实现缺陷路由到
Generator，需求/范围问题路由到 Planner。
