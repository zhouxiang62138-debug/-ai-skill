# F12 EXPERIMENTAL PROTOTYPE：安全边界报告

> F10R 范围更正：本文件描述的实现已移入 `experimental/f12_security_boundary/`，
> 不属于正式 Runtime，不构成 Credential Proxy、凭据签发或网络安全边界；以下历史性描述
> 不能作为已交付能力或安全承诺。

已实现角色到工具/域名的最小能力策略、受控 Credential Proxy、敏感环境变量剥离、疑似
凭据字段扫描与 Prompt Injection 拒绝。凭据不进入执行环境、Event Payload 或代理结果。

限制：当前 Proxy 是可注册 handler 的本地接口；真实外部服务的短期凭据签发与网络代理
部署需由运行环境接入该接口，不能通过 Prompt 代替。
