# E1 Evaluator Independence Hardening 实现报告

## 结论

本次升级独立命名为 **E1 Evaluator Independence Hardening**。它是“验收可信度”升级，与 F11 Execution、F12 Security、F13 Context 并列，不属于其中任何一个阶段，也没有新增第四个 Agent。

当前实现覆盖三类边界：

- Runtime 负责创建新的 Evaluator Model Invocation，并把 revision、Context Manifest、代码快照和模型能力元数据绑定到调用记录。
- ContextBuilder 为 Evaluator 生成 `EVALUATOR_INDEPENDENT` 上下文，排除 Generator 私有响应、推理、自评和历史 freeform 对话；事实型 handoff 仍可作为待核验输入。
- Runtime Verifier 只接受带有独立复现、来源类型、Tool Call、Tool Attempt、结果哈希、时间戳、revision 和代码快照的结构化证据。

## 已落地组件

| 组件 | 实现 |
| --- | --- |
| E1 策略 | `config/evaluation_independence.yaml`，明确 Fresh Invocation、排除源、来源枚举、复现要求和失败码 |
| 调用元数据 | `runtime/session_store.py` 的 `model_invocations` 增加 Context Manifest、revision、model、capability、fresh context 和隔离级别字段 |
| Fresh Invocation | Evaluator 启动前拒绝同一 Session 中仍为 ACTIVE 的旧 Evaluator Invocation；不继承上一调用的 freeform 历史 |
| 独立上下文 | `runtime/context/models.py`、`runtime/context/builder.py`、`runtime/context/policy.py` 增加上下文类型和排除源检查 |
| 证据来源 | `runtime/evaluator_independence.py` 要求关键 PASS 必须来自 `EVALUATOR_REPRODUCED` 或 `RUNTIME_VERIFIED` |
| 执行反查 | 正式 Orchestrator 路径把 Session Store 注入 Verifier，反查 Tool Call / Tool Attempt 的成功状态、结果哈希、代码快照和不可变结果引用 |
| 确定性门禁 | `runtime/verifiers.py` 把 E1 作为 Evaluator 必需步骤；不满足即不能生成 PASS Attestation |
| 写入边界 | 保持 Evaluator 禁止写 `code/`、`config/evaluation_rules/`、计划和需求；现有 Path Policy 与 CAS 继续生效 |
| 文档与 Prompt | `SKILL.md`、Runtime/Session/Orchestrator/Workflow 文档和 Evaluator/Generator Prompt 均明确 E1 独立定位 |

## 失败闭环

以下任一情况都会 fail closed：Fresh Invocation 不匹配、上下文类型错误、Generator 只提供自评、旧 revision 或旧代码快照、复现项失败、关键标准缺少独立证据、Tool Attempt 不存在或结果哈希不一致、存在 blocking/critical issue。

异常不会删除历史记录。Invocation、Tool Attempt、Context Manifest、Event 和 Attestation 仍由 F10 durable Session Store 保存；重复请求通过既有幂等键和 replay 逻辑恢复，不重复生成业务工件。

## 限制

Runtime 能证明自己的上下文和执行记录隔离，但不能凭空控制宿主聊天窗口的对话历史。当前 `context_isolation` 会明确标记为 `RUNTIME_CONTEXT_ONLY`；只有适配器声明支持宿主隔离时才标记为 `HOST_AND_RUNTIME`。

