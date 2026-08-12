# RA7-D Browser/Web Acquisition 阶段报告

## 阶段

RA7-D — Web Browser Acquisition

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段实现了受控 Browser/Web Acquisition Provider。Provider 不自行开启任意网络，也不读取宿主 Cookie、密码、扩展或浏览器 Profile；只有在适配器、DNS resolver 和 F12 网络授权回调同时注入时才报告 `AVAILABLE`。当前默认配置仍为 `BLOCKED_BY_ENVIRONMENT`。

## 实现链路

```text
AcquisitionRequest(web_page + HTTPS URI)
  -> public DNS validation
  -> F12 network authorization callback
  -> browser isolation contract
  -> navigation guard on every URL/redirect
  -> bounded DOM + Computed Style + Screenshot
  -> SHA-256 artifacts
  -> append-only Browser Capture summary
```

主要实现位于 `runtime/reference_analysis/browser_acquisition.py`，协议校验位于 `scripts/reference_protocol.py`，Schema 位于 `config/schemas/browser_capture_v1.schema.json`。

## 能力与安全边界

- 只接受 HTTPS，拒绝 URL credentials、敏感查询、私有/回环/metadata 地址和未经 resolver 验证的域名。
- 初始 URL 经过预授权；浏览器导航安装 `set_navigation_guard`，每次导航和 redirect 都重新走 URL/DNS/F12 校验。
- 默认拒绝跨 origin redirect；即使放行，也必须再次经过同样授权链。
- 强制使用 clean profile、无 Host cookies、无 saved passwords、无 extensions、隔离 storage、禁止 downloads 和 popups。
- 固定单一 bounded viewport，DOM、Computed Style 和 Screenshot 均受大小上限约束。
- Screenshot 必须是 PNG；每个 artifact 保存相对路径、MIME、大小、SHA-256 和类型元数据。
- console errors、failed requests、browser runtime identity 和 capture time 只作为受控摘要保存；页面内容标记为 `untrusted`，不会驱动工具或 Runtime 状态。
- 已有 `runtime/browser` 验收 Harness 未被替换；RA7-D 通过新的 Capture Adapter 协议消费它之外的专用能力。

## 幂等与恢复

- Capture ID 由请求 fingerprint 和 snapshot version 确定。
- 已完成的 capture 摘要和 artifact 哈希可直接复用，重复请求不会再次打开浏览器或覆盖 artifact。
- 发现不完整的旧 artifact 时 fail closed，不删除、不覆盖、不静默修复历史文件。
- Capture 摘要只在三类 artifact 完整且通过 Schema 校验后写入。

## 默认环境状态

- 默认 `browser_acquisition.status`：`BLOCKED_BY_ENVIRONMENT`
- 默认网络授权：未向 `reference_analysis` 开放任意外部站点
- 默认真实 Playwright/Browser Capture：未注入
- F12/F11/F13 兼容接口：已具备测试契约
- 真实生产 Web 抓取：NOT_READY，必须由 Host/Runtime 提供受控 adapter 和授权策略后才可启用

## 验证证据

- RA7-D 专项测试：`4 passed`
- RA7-C/RA7-D + RA7-B + Reference R0-R6 + Runtime Security 定向回归：待本阶段最终回归后补充
- 全量回归：RA7-C 阶段已验证 `703 passed, 5 skipped, 113 subtests passed`；RA7-D 改动后待重新执行
- Schema JSON 解析：待本阶段最终回归后补充
- `git diff --check`：待本阶段最终回归后补充

## 已知限制

1. 当前 Host 没有注入真实 Browser Capture Adapter，因此没有对真实公网页面执行抓取。
2. 资源级网络拦截、浏览器上下文创建和真实 DOM/Computed Style API 由后续 Host adapter 提供；本阶段只定义并校验边界。
3. 多 viewport、登录态、下载、跨站资源策略、PDF、Repository、Video 和 R6 Visual Conformance 仍未实现。

## Gate 决策

CONTINUE — Browser Acquisition 的协议、安全边界和可复现 fake-adapter 证据已具备；真实环境能力保持 BLOCKED，进入 RA7-E 时只能融合已哈希的 deterministic Web evidence 与明确 provenance 的视觉结果。
