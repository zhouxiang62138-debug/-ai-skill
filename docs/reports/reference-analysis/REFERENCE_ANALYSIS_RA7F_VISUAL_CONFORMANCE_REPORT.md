# RA7-F Visual Conformance Provider 阶段报告

## 阶段

RA7-F — Visual Conformance Provider for R6

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段新增 `VisualConformanceProvider`，只消费批准的 `REFDEC-*` 绑定和两份不同的、已校验 SHA-256 的截图。Provider 可以通过受控 Native Perception bridge 产生结构化比较 Finding，但运行摘要只能是 `BLOCKED` 或 `UNVERIFIED`，不拥有 R6 Gate 的 PASS/FAIL 决策权。

## 受控链路

```text
approved REFDEC binding
  + reference screenshot hash
  + implementation screenshot hash
  -> VisualComparisonRequest
  -> Native Multimodal Perception Provider
  -> structured comparison Finding
  -> VisualConformanceRun(UNVERIFIED)
  -> existing R6 deterministic evidence/gate
```

## 安全与一致性规则

- 没有批准绑定、只有一张图片、两张图片相同或跨 Reference 时直接拒绝。
- 两份截图都必须通过项目内路径、MIME、文件内容 SHA-256 校验。
- Provider 输出必须通过现有 `reference_finding_v1` 校验，且 Finding 必须引用两份截图证据。
- 精确像素 PASS/FAIL、自动验收决定和自动阈值修改在输入 exclusions 中明确禁止。
- Host/Native Perception 不可用时返回 `BLOCKED` 和 limitation，不伪造视觉 Finding。
- 视觉比较结果不会写入 `project.yaml`、修改 R6 阈值、改变 approved contract 或替代 deterministic DOM/CSS evidence。

## 实现与协议

- `runtime/reference_analysis/visual_conformance.py`
- `scripts/reference_protocol.py:validate_visual_conformance`
- `config/schemas/visual_conformance_v1.schema.json`
- `runtime/reference_analysis/__init__.py` 公共导出视觉比较对象

## 验证证据

- RA7-F 专项测试：`3 passed`
- RA7-E + RA7-F 专项测试：`7 passed`
- 全量回归：待最终 Gate 回归后补充
- Schema JSON 解析：待最终 Gate 回归后补充
- `git diff --check`：待最终 Gate 回归后补充

## 已知限制

1. 当前 Host 没有注入真实生产 Multimodal bridge，默认视觉比对仍为 `BLOCKED`。
2. Provider 只产生结构化比较观察，不做像素差分、阈值判断、自动修复或 R6 gate decision。
3. R6 现有 Evaluator 流程和 visual capability matrix 保持不变；正式接线需要后续批准的集成变更。

## Gate 决策

CONTINUE — Visual Provider 已有批准绑定、双截图哈希、结构化输出和 no-gate-authority 契约；生产视觉能力仍按真实环境状态 BLOCKED，继续进入 RA8 前需完成最终回归和自审计。
