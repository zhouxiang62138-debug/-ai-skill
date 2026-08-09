# Stage 2 — Feature Completeness / Anti-Stub 设计

日期：2026-08-09

## 目标

阻止“界面存在、按钮存在，但核心功能没有真实行为”的假完成。Feature Gate 不只
搜索 TODO，而是把静态代码证据、Runtime 验证、Browser 行为和 Requirement / AC
关联记录合并判断。

## 设计

```text
static scanner
      + runtime observation
      + browser observation
      + requirement / AC links
                    ↓
Feature Completeness Evaluator
                    ↓
GATE-FEATURE-COMPLETENESS
```

- `runtime/completeness/` 只提供确定性扫描、观察记录模型和 Gate 计算，不修改代码。
- 静态扫描覆盖 TODO/FIXME、placeholder、明显 mock/fake 数据、空回调和空处理器等
  可复现信号；每条信号有路径、行号、类别和稳定 Finding ID。
- 观察记录必须关联 Requirement ID、Acceptance Criterion ID 和证据引用，能够表达
  “点击成功但刷新后数据丢失”等部分实现。
- Profile 声明 `minimum_score` 和必需证据来源；`web_app` 需要 static/runtime/browser，
  `default` 不强制该 Gate。

## 安全与兼容

- 只读扫描项目 `code/`，不读取 Secret、Control Plane 或项目外路径。
- 结果只追加进 Evidence Manifest；不写 `project.yaml` 正文，不改变现有评分权重。
- 旧 manifest 没有 Feature 字段时保持兼容；Profile 未启用时 Gate 为 SKIPPED。
