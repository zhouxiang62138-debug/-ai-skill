# F14-D Context Escalation Report

报告日期：2026-08-12
实施模式：Shadow / Controlled Context Escalation
最终结论：**PASS**
停止边界：完成 F14-D 后停止，不进入 F14-E

## 1. 范围和不变量

本阶段实现了：

- 结构化 `ContextRequest`；
- Runtime 生成的 `ContextAuthorization`；
- L1 / L2 / L3 progressive loading；
- 受控 `Expansion Loop`；
- F13 Full Context 恢复；
- F10 外部 Session Store 的追加式请求、授权、扩展和恢复证据。

以下不变量保持不变：

- F13 正式 Context 仍是正式模型输入；
- 默认 Selective Context 未启用；
- Context Expansion 不减少正式 LLM Invocation；
- Expansion Loop 本身的 `llm_invocations` 固定为 0；
- 不接受模型传入的任意文件路径；
- 不进入 F14-E Incremental Context / Summary Cache。

## 2. Context Request

`ContextRequest` 绑定：

- `request_id`、`session_id`、`run_id`、`role`；
- 当前 `project_revision`；
- 当前 F13 `context_hash`；
- 缺失依赖引用 `missing_dependency_refs`；
- 目标来源类型 `desired_source_kinds`；
- `requested_level`、原因、紧急度；
- 单次预算和扩展次数；
- Artifact Index Snapshot 与 Dependency Graph Snapshot ID。

请求只接受 artifact/dependency ID，不接受 `code/foo.py` 这类任意 locator。请求参数、revision、hash、预算、角色、层级和 Secret 相关字符串均做确定性校验。

## 3. Runtime Authorization

授权不信任调用方声明，而是由 Runtime 重新验证：

1. 当前 Session 与项目根目录绑定；
2. `project.yaml` 的 Runtime revision 与请求一致；
3. Artifact Index / Dependency Graph snapshot 的 project、revision、policy hash 一致；
4. 请求 ID 存在于 Artifact Index；
5. explicit Dependency Graph 闭包可解析；unknown edge 不会被当作无影响；
6. artifact freshness 为 `CURRENT`；
7. authority 属于可验证集合；
8. L3 禁止只凭 `GENERATED_ARTIFACT` 进入受控原文层；
9. Role Capability `filesystem.read` 通过；
10. 最终 locator 重新经过 F11/F12 Path Policy。

授权结果只有：

- `APPROVED`：可以继续受控交付；
- `DENIED`：权威证据表明不可交付；
- `UNAVAILABLE`：Index/Graph 计算或存储不可用，不能假装 Coverage 完整。

## 4. L1 / L2 / L3

| 层级 | 含义 | 交付 |
|---|---|---|
| L1 | 最小事实层 | artifact ID、kind、locator、hash、来源层级；只给 Reference，不读入原文 |
| L2 | 任务证据层 | 通过 hash、Secret Scan、Path Policy 后交付受控原文 |
| L3 | 受控原文/历史层 | 仅允许已验证 authority 的原文；生成物不能单独升级为 L3 |

层级不是按文件大小切片，而是按来源 authority、任务依赖、revision 和可验证性决定。每次交付都会记录层级、来源、bytes、授权 ID 和正式 Context 未改变的事实。

## 5. Expansion Loop

`run_expansion_loop()` 执行以下约束：

- 扩展次数受 `max_expansions` 限制；
- 累计交付 bytes 受全局和 request budget 限制；
- request 必须绑定同一个当前 F13 Context hash；
- request 的 `expansion_count` 必须与循环位置一致；
- 重复 request ID 或重复依赖引用触发 `EXPANSION_CYCLE`；
- 每次扩展后保持正式 Context 对象不变；
- 扩展流程不会创建模型调用，`llm_invocations=0`。

扩展成功只产生受控旁路证据，不把候选结果自动合并进正式 F13 Package。

## 6. 恢复机制

以下情况恢复到原 F13 Full Context：

- Index / Graph unavailable；
- 扩展读取失败；
- source hash mismatch；
- Secret Scan 命中；
- expansion budget 超限；
- revision 过期；
- unknown dependency；
- cycle / binding mismatch；
- Role Path Policy 拒绝。

恢复记录保存 `fallback_context_hash`，并强制 `preserved_formal_context=true`。恢复路径不生成 LLM Invocation，不清空 Context，不删除历史证据。

## 7. 审计与存储

F10 Runtime schema 从 v11 增至 v12，新增外部 Control Plane 追加式表：

- `f14_context_requests`；
- `f14_context_authorizations`；
- `f14_context_expansions`；
- `f14_context_recoveries`。

所有表有完整性 hash、幂等 ID 和 append-only trigger。事件包括：

- `CONTEXT_REQUESTED`；
- `CONTEXT_AUTHORIZED`；
- `CONTEXT_EXPANDED`；
- `CONTEXT_EXPANSION_DENIED`；
- `CONTEXT_RECOVERED`。

事件只保存受控引用、数量、状态、hash 和原因摘要；敏感原因会转为稳定 hash 引用，不把 Secret 或原始敏感内容写入 Event Payload。

## 8. F14-D 测试证据

F14-D 专项：

```text
python -m unittest tests.test_f14_d_context_escalation -v
Ran 7 tests ...
OK
```

覆盖内容：

- L1 / L2 / L3 progressive loading；
- Shadow-only delivery 与正式 F13 Context 保持不变；
- indexed ID 解析与未知依赖拒绝；
- Index unavailable → F13 fallback；
- stale revision；
- Role Path Policy；
- Secret Scan；
- budget denial；
- L3 authority 防护；
- unknown dependency 不静默省略；
- expansion cycle → F13 recovery；
- append-only audit 与事件证据。

F14-D 专项结果：**7/7 PASS**。

## 9. 回归结果

| 集合 | 结果 |
|---|---:|
| F14-D focused | 7/7 |
| F14-B focused | 30/30 |
| F14-C focused / bridge / Post-Verification | 44/44 |
| 原有重点回归 | 120/120 |
| 完整 `python -m unittest discover -s tests -p 'test*.py'` | 481/481 |
| `git diff --check` | PASS |

完整 suite：

```text
Ran 481 tests in 31.172s
OK
```

## 10. F14-E 决策

F14-D：**PASS**。

F14-E：**NO-GO / NOT STARTED**，原因是本次用户授权明确要求不进入 F14-E。当前停止条件：

- 不启用默认 Selective Context；
- 不减少正式 LLM Invocation；
- 不引入 Incremental Context；
- 不引入可替代权威原文的 Summary Cache；
- 等待用户对 F14-E 的单独授权。

