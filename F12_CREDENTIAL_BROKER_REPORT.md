# F12.2 Credential Broker

## Result

`PASS`

## Implemented

- 新增 Host-side `runtime/security/credentials.py` 与无凭据 `CredentialRequest` 模型。
- 注册信息只在 Broker 进程内存中保存；配置文件不保存 Secret。
- provider 只接收结构化请求和 Host-side Secret，Role、ExecutionContext、ExecutionRequest、Backend 不接收 Secret。
- 支持 `service`、`credential_id` 和 `allowed_operations` 最小权限校验。
- provider 返回值执行已知 Secret 精确脱敏，并拒绝敏感字段和 Secret key。
- provider 异常只返回稳定错误，不传播异常文本。
- ExecutionBroker 新增 `request_credential` Host-side 调用路径。
- `CREDENTIAL_REQUESTED`、`CREDENTIAL_ALLOWED`、`CREDENTIAL_DENIED` 复用 F10 Event 审计。

## Security Boundaries

- Generator 获得 `credential.use` Capability；Planner 和 Evaluator 默认拒绝。
- 未实现 Secret Vault、KMS、真实 OAuth、Network Proxy、Browser Proxy 或 Docker Sandbox。
- Event、异常和审计回调不记录 Secret、Token、Password 或 API Key。

## Tests

- F12.2 定向测试：21 passed。
- 全量回归：453 passed, 5 skipped, 104 subtests passed。
