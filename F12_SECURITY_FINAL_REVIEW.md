# F12 Security Final Review

## Capability

- `DENY BY DEFAULT` 由 `CapabilityPolicy` 代码强制。
- Planner、Generator、Evaluator 的 Capability 来自 `config/role_policies.yaml`。
- ExecutionBroker 在执行、读写文件及 F12 受控入口前检查 Capability。
- Path Policy 与 F10 CAS 仍独立生效，Capability 不能绕过它们。
- 正式 Agent 仍只有 Planner、Generator、Evaluator。

## Credential

- 正式顺序为 `Role → Capability → Credential Broker → Provider`。
- credential/service/operation allowlist 默认拒绝，Secret 仅由可信 Host-side Broker 在内存中持有。
- Provider 返回值和异常使用稳定错误/已知 Secret 精确脱敏；Audit 不保存 Secret。
- Role、ExecutionContext、ExecutionRequest、ExecutionEnvironment、workspace、Event、Tool Result、`project.yaml`、异常和日志不接收真实 Secret。

## Network / Tool

- Managed network/external-tool 路径均先检查 Capability，再检查 Host/Tool 与 operation allowlist；需要凭据时才调用 Credential Broker。
- Network 默认拒绝，使用 exact host、scheme、port 校验，并阻断 localhost、loopback、private、link-local、metadata 地址。
- Redirect 目标重新执行完整授权；External Tool 使用 tool + operation allowlist，默认拒绝。

## Audit

- F12 复用 F10 `EventType` 与 `SessionStore`，未创建第二套安全 Event Log。
- Capability、Credential、Network、External Tool 的拒绝/允许事件只记录安全元数据、稳定错误码和关联信息，不记录 Secret、Authorization header 或敏感 query。

## Known limitations

- Managed capability/network/credential paths: **ENFORCED**。
- Local arbitrary subprocess host/network isolation: **NOT PHYSICALLY ENFORCED**。
- `LocalCompatibilityEnvironment` 不是 Secure Sandbox；本地进程主动联网不能被当前 F12 从 OS 层物理阻断。
- Docker Sandbox: **DEFERRED**。

## Tests

- F12/F10/F11 关键回归：`51 passed`。
- Full regression：`475 passed, 5 skipped, 104 subtests passed`。
- 5 个 skipped 全部是 Docker daemon unavailable；对应 F11 Docker Sandbox deferred，不计入 F12 PASS 证据。

## Final verdict

- F12 Managed Security Boundary: **PASS**
- Physical Local Sandbox Isolation: **NOT PROVIDED**
- Docker Sandbox: **DEFERRED**
