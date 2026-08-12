# F12.1 Capability Policy

## Result

`PASS`

## Implemented

- 在 `config/role_policies.yaml` 中声明正式 Capability 集合及 planner、generator、evaluator 的授权集合。
- 在 `runtime/policy.py` 中实现配置驱动的 `CapabilityPolicy`、`authorize` 和 `check`。
- 默认拒绝未知 Role、未知 Capability 和未声明的 Role Capability。
- 为 Capability 拒绝增加 `CAPABILITY_DENIED` F10 Event；审计内容只保留稳定标识与 hash。
- ExecutionBroker 在执行、读文件、写文件、列文件、快照、恢复和终止前执行 Capability 检查。

## Preserved Boundaries

- 现有 Path Policy、Role Policy、F10 Lease/CAS/Session 约束保持有效。
- Capability 数据、拒绝 Event 和审计内容不保存真实 Token、Password 或 API Key。
- 未实现 Credential Broker、Vault、Network Proxy、Browser Proxy 或 Docker Sandbox。

## Tests

- F12.1 定向测试：13 passed。
- 全量回归：445 passed, 5 skipped, 104 subtests passed。
