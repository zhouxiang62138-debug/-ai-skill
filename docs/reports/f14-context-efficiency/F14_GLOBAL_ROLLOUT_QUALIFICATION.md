# F14 Global Production Rollout Qualification

报告日期：2026-08-12

## 最终结论

| 资格项 | 结论 |
| --- | --- |
| Protocol Drift | PASS |
| F14 Engineering | PASS |
| Real Model Qualification | BLOCKED |
| Real Browser Qualification | BLOCKED |
| Evaluator Selective Qualification | BLOCKED |
| Global Rollout | NOT ENABLED |
| F13 Emergency Fallback | READY |

当前生产策略是安全的 Controlled 状态：正式配置仍为 `f13_full` 默认交付，F14 Selective、Evaluator Selective 和 Invocation Gate 均关闭；F14 资格状态记录为 `CONTROLLED_QUALIFIED`，Global 标志为 `false`。

## Controlled 工程资格

工程侧已通过：

- Protocol manifest、一致性 checker 和协议故障注入测试通过。
- 角色只有 Planner、Generator、Evaluator；四个正式 Module 不会成为第四个 Agent。
- Research、Reference Analysis、Product Approval、Plan Approval 和 Generator 来源链均有独立路由或门禁。
- F14 rollout 状态机拒绝越级 Global，支持逐级证据晋级和 F13 回退。
- Kill Switch、项目/角色/阶段覆盖、Selective builder 异常和不确定性均 fail closed。
- 完整 unittest：509/509 PASS。
- 完整 pytest：930 passed、6 skipped、129 subtests passed。
- F14 focused pytest：16/16 PASS。

历史 controlled A/B 证据仍保持原结论：Case C 的净 Context bytes 为 2200，净下降 22%；重复 Context 下降 2500 bytes。该数据来自 controlled fixture，不能换算成真实 token，也不能证明真实 Host 请求减少。

## Real Model Qualification：BLOCKED

当前 Host 证据：

- `openai_codex` SDK：可导入，版本 `0.144.4`。
- Host 账户状态：`unavailable`。
- 认证状态：`existing_codex_auth_not_available`。
- 本轮真实模型请求：`0`；这代表没有执行，不代表节省了请求。
- `input_tokens`、`cached_input_tokens`、`output_tokens`、`total_tokens`、latency、model identity 和 invocation id：全部 `UNAVAILABLE_FROM_HOST`。

因此不能把 controlled adapter、fixture bytes 或零请求运行伪装成 Real Model Qualification，也不能声称真实 token saving。

## Real Browser Qualification：BLOCKED

已按浏览器控制技能连接 Codex In-app Browser 并读取当前 Tab 状态：Tab 列表为空。当前仓库是 Skill 本体，不是 Case C managed project；没有真实 Gold App target、用户登录状态、导航路径或视觉验收对象。

本轮没有创建替代 HTML fixture，也没有把静态/模拟浏览器测试冒充 Real Browser Gold E2E。历史 controlled browser harness 证据继续保留，但不能升级为真实浏览器资格。

## Evaluator Selective Qualification：BLOCKED

Evaluator Selective 必须独立于 Generator，并且需要真实 Model/Browser 资格和质量 parity 证据。本轮 `evaluator_selective`、`quality_parity` 均为 `BLOCKED`，所以不能打开 Evaluator Selective，也不能把受控检测差异为零直接解释成生产 parity。

## 唯一剩余 Global Blocker

`BLOCKER-F14-GLOBAL-001: REAL_GOLD_QUALIFICATION_ENVIRONMENT_UNAVAILABLE`

含义：当前执行环境没有可用的已认证真实 Codex Host usage 通道，也没有可运行的真实 Case C Browser Gold target；因此缺少 Global Enabled 所需的 Level-1 Real Model、Real Browser 和独立 Evaluator 资格证据。代码工程侧没有借此伪造通过，Global 必须保持关闭。

## F13 Emergency Fallback：READY

- 正式默认交付为 `f13_full`。
- F14 Context Delivery Service 在优化异常、覆盖失败或不确定性下保留当前 F13 Context。
- Rollout Policy 的自动回退固定为 `f13_full`。
- Kill Switch 和 `F14FeatureFlags.rollback()` 均返回 F13 安全状态。
- 本轮 rollout governance、ContextBuilder 和 SourceCache 回归均通过。

## 当前配置快照

```yaml
rollout:
  mode: controlled
  qualification_status: CONTROLLED_QUALIFIED
  fallback_mode: f13_full
  automatic_fallback: true
  global_enabled: false
qualification_evidence:
  controlled: PASS
  real_model: BLOCKED
  real_browser: BLOCKED
  evaluator_selective: BLOCKED
  quality_parity: BLOCKED
  fault_injection: PASS
  fallback: PASS
```

详细机器结果见同目录的 `F14_GLOBAL_ROLLOUT_RESULTS.json`。
