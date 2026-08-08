# F12.3 Network / External Tool Policy

## Result

`PASS`

## Network

- `network_policy` 与 `external_tool_policy` 复用 `config/role_policies.yaml`。
- 默认拒绝；Network Policy 精确检查 service、operation、scheme、host 和 port。
- 默认只允许 HTTPS；localhost、loopback、private、link-local、metadata、unspecified 和 multicast 地址拒绝。
- redirect 目标通过 `authorize_redirect` 重新执行完整授权。
- URL 中的 userinfo、fragment 和敏感 query 参数拒绝；不把 URL 写入 Event。

## External Tools

- tool allowlist 与 role tool allowlist 均由配置驱动。
- operation 必须命中工具 allowlist；不接受 argv、shell 或 raw command。
- ExecutionBroker 的顺序为 Capability → External Tool Policy → Credential Broker → Handler。
- F10 Event 记录 role、capability、service/tool、operation、host、decision 和 correlation_id，不记录 Secret。

## Known Limitation

`Local arbitrary subprocess networking is NOT physically sandboxed.`

F12.3 只强制托管 Network/External Tool 调用；LocalCompatibilityEnvironment 仍是兼容模式，不能宣称具备 OS 级联网隔离。Docker 继续 DEFERRED。

## Tests

- F12.3 定向测试：43 passed。
- 全量回归：475 passed, 5 skipped, 104 subtests passed。
