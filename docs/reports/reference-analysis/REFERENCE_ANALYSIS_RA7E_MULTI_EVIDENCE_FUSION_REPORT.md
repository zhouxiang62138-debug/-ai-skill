# RA7-E Web Multi-Evidence Perception/Fusion 阶段报告

## 阶段

RA7-E — Web Multi-Evidence Perception and Fusion

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段实现了 `ReferenceFusionEngine`，把 deterministic DOM/CSS/viewport 观察与带 provenance 的视觉 Finding 放进同一个融合契约。确定性观察优先，但视觉冲突不会被静默覆盖；不可用、失败或阻断的视觉能力不能满足绑定要求。

## 融合规则

- 输入必须属于同一个 `REF-*`，视觉 Finding 必须绑定成功的 `PER-*`。
- 按 domain/category 对齐 deterministic 与 visual 来源。
- deterministic 值与视觉值冲突时，`selected_source_refs` 只选择 deterministic 来源，同时在 `conflicts` 中保留两侧引用、冲突原因和 `review_required: true`。
- 缺少 requested domain、视觉能力 `UNAVAILABLE`、`FAILED` 或 `BLOCKED` 时，结果为 `binding_status: BLOCKED`，只记录 limitation，不生成替代 Finding。
- 有冲突但输入域完整时，结果为 `REVIEW_REQUIRED`，不自动决定产品取舍。
- 输出保存输入哈希、来源引用、冲突和限制项，信任级别固定为 `untrusted`。

## 实现与协议

- `runtime/reference_analysis/fusion.py`
- `scripts/reference_protocol.py:validate_reference_fusion`
- `config/schemas/reference_fusion_v1.schema.json`
- `runtime/reference_analysis/__init__.py` 公共导出 `ReferenceFusionEngine`

## 验证证据

- RA7-E 专项测试：`4 passed`
- RA7-B/RA7-C/RA7-D + Reference R0-R6 + Browser/Runtime Security 定向回归：待最终 Gate 回归后补充
- 全量回归：RA7-D 阶段已验证 `703 passed, 5 skipped, 113 subtests passed`；RA7-E 改动后待重新执行
- Schema JSON 解析：待最终 Gate 回归后补充
- `git diff --check`：待最终 Gate 回归后补充

## 已知限制

1. 本阶段提供融合协议和规则，不自动抓取网页、不自动调用模型，也不改写 R6 验收阈值。
2. Fusion summary 需要由后续受控的 Reference Artifact 流程持久化；当前核心接口先提供可校验、可哈希记录。
3. 多模型路由、概率校准、自动冲突裁决、跨 viewport 融合仍未实现。

## Gate 决策

CONTINUE — deterministic precedence、conflict preservation 和 unavailable blocking 已有可复现测试，进入 RA7-F 只消费批准绑定和两份已哈希截图，不把视觉推断伪装成 R6 PASS/FAIL。
