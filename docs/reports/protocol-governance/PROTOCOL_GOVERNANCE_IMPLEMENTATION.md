# Protocol Governance Implementation

报告日期：2026-08-12

## 实现结果

本轮把“协议一致”从文档约定提升为可执行的 fail-closed 检查：任何权威配置、Runtime 路由、资格门禁或当前状态标记不一致，检查器都会返回失败，不允许继续把不确定状态当成生产资格。

### Authority 与漂移检测

- `config/protocol_manifest.yaml`：机器可读的协议索引。
- `scripts/protocol_consistency.py`：校验版本、schema 继承、角色/Module 集合、状态路由、迁移别名、研究顺序、审批来源链、重试策略、Change Request 路由、文档状态引用和 F14 生产标记。
- `tests/test_protocol_consistency.py`：对版本漂移、缺失状态路由、第四个 Agent、合并 Product/Plan 门禁和未经资格的 Global 声明做故障注入。

### F14 Rollout 状态机

`runtime/f14_control.py` 新增受约束的资格阶梯：

```text
IMPLEMENTED
  → CONTROLLED_QUALIFIED
  → REAL_MODEL_QUALIFIED
  → GLOBAL_READY
  → GLOBAL_ENABLED
```

任意阶段都允许回到 `FALLBACK_F13`；晋级必须满足当前阶段所需的证据键和值为 `PASS`。`global_enabled` 只有在 `mode=global` 且 `qualification_status=GLOBAL_ENABLED` 时才允许为真，构造时即拒绝越级声明。

### Kill Switch、覆盖和默认值

- 运行时对象默认资格为 `IMPLEMENTED`，默认交付仍为 `f13_full`。
- 正式配置当前为 `mode=controlled`、`qualification_status=CONTROLLED_QUALIFIED`，但 `selective_context`、Evaluator Selective 和 Invocation Gate 均保持关闭。
- 手动 Kill Switch、disabled project、disabled role、disabled phase 任一命中，均阻止 F14 交付。
- `InvocationGate.from_f14_config()` 只读取正式 F14 配置，资格不足或开关关闭时不会自行启用。
- `F14FeatureFlags.rollback()` 返回 `FALLBACK_F13` 安全状态，不修改磁盘配置或历史记录。

### 跨层错误码协议

SourceCache 是 Context 的优化层，不是新的业务协议层。Secret 在缓存读取路径中统一抛出 `CONTEXT_SECRET_FORBIDDEN`，与直接 ContextBuilder 路径一致；缓存相关测试仍验证 `SECRET_FORBIDDEN` 这一安全类别。

## 验收证据

| 验证 | 结果 |
| --- | --- |
| Protocol consistency checker | PASS |
| Protocol drift unit tests | 6/6 PASS |
| F14 rollout governance tests | 6/6 PASS |
| F14 focused pytest | 16/16 PASS |
| ContextBuilder + SourceCache regression | 11/11 PASS |
| Full unittest discovery | 509/509 PASS |
| Full pytest | 930 PASS，6 skipped，129 subtests PASS |
| `git diff --check` | PASS |

## 生产边界

本实现已完成 Controlled Qualification 所需的工程治理和 F13 回退准备，但没有因为工程测试通过就打开 Global。Real Model、Real Browser 和 Evaluator Selective 的独立资格仍由外部能力证据决定，缺失时必须保持 `NOT ENABLED`。
