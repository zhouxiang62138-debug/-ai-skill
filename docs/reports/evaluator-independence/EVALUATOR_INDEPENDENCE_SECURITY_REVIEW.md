# E1 Evaluator Independence Hardening 安全审查

## 审查结论

结论：**PASS（带宿主隔离限制）**。E1 已把“Generator 说通过”从最终验收依据降级为待核验声明，并在正式 Runtime 路径中要求可反查的执行记录。

## 威胁与控制

| 威胁 | 控制 | 结果 |
| --- | --- | --- |
| Generator 自评冒充验收结果 | `GENERATOR_PROVIDED` 不能支撑关键 PASS；必须有 Evaluator 或 Runtime 来源 | PASS |
| 复用旧 Evaluator 上下文 | 每次 Evaluator Invocation 绑定新的 Context Manifest；ACTIVE 旧调用会阻止新调用 | PASS |
| 使用旧代码或旧 revision 的证据 | 证据与当前 revision、代码快照哈希逐项绑定 | PASS |
| 伪造 Tool Call / Attempt | 正式 Verifier 反查 Session Store，并校验成功状态、归属 Session、Attempt、结果哈希和结果引用 | PASS |
| 结果文件被替换 | Tool Result 由 Session Store 保存并按 SHA-256 校验；引用必须落在受控目录 | PASS |
| Evaluator 篡改代码、计划或验收规则 | Execution Path Policy、role policy 和 CAS 写边界共同拒绝 | PASS |
| 通过宿主聊天历史泄漏 Generator 私有内容 | Runtime 过滤已知私有来源和响应路径 | 部分可控 |
| 宿主窗口本身继续保留旧对话 | Runtime API 无法控制外部宿主会话 | UNAVAILABLE |

## 残余风险

E1 仍依赖 Model Adapter 把实际复现交给受控 Execution Broker，并返回对应的 Tool Call / Attempt 标识。若适配器绕过正式 Runtime，宿主系统必须拒绝将其结果当作正式验收证据；E1 已在正式 Orchestrator 路径上设置该反查门禁。

## 安全边界声明

E1 不修改 F11 的执行环境实现，不扩大 F12 的凭据或网络权限，也不替代 F13 的上下文预算治理。三者为 E1 的依赖能力；E1 只负责判断验收证据是否独立、可追溯且足以触发确定性 PASS。

