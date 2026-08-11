# RA7-B Acquisition Core & Runtime Safety 报告

## Phase

RA7-B — Acquisition Core & Runtime Safety

## Status

PASS — READY_WITH_LIMITATIONS

本阶段在现有 R0–R6 Reference Analysis、F10 Session/Lease/CAS、F11 Execution、F12 Security 和 F13 Context 边界上追加 Acquisition Core。没有新增 Agent，没有新增第二套 CAS，也没有直接写入 `project.yaml`。

## Architecture

正式链路为：

```text
AcquisitionRequest
  -> AcquisitionProviderRegistry
  -> ProviderCapability / ProviderAvailability
  -> AcquisitionManifestStore
  -> Provider acquire
  -> immutable artifact metadata
  -> Reference Evidence extension
```

`runtime/reference_analysis/acquisition.py` 提供统一请求指纹、Provider 能力模型、状态生命周期、追加式 Manifest、Retry/Refresh 语义、崩溃恢复和浏览器隔离契约。Provider 只能返回结构化工件摘要，不能返回 `status`、`next_role`、CAS、approval 或 `project.yaml` patch。

## Acquisition Contract

- 支持 `REQUESTED`、`STARTED`、`SUCCEEDED`、`FAILED`、`TIMED_OUT`、`CANCELLED`、`UNKNOWN_AFTER_CRASH` 生命周期。
- Retry 保留同一个 `acquisition_id`，递增 `attempt`。
- Refresh 创建新 `snapshot_version` 和新 `acquisition_id`，通过 `refresh_of` 保留来源链。
- 请求指纹只包含受控 locator、scope、context、Provider 和 snapshot 元数据，不保存原始 HTML、图片字节或其他正文。
- Manifest 采用追加式 YAML 快照，重复请求按指纹幂等复用；已 STARTED 的记录可恢复为 `UNKNOWN_AFTER_CRASH`。

## Provider Registry

Provider 可用性明确区分：

```text
SUPPORTED
AVAILABLE
UNAVAILABLE
BLOCKED_BY_ENVIRONMENT
```

当前正式可用 Provider 为 `local-image-acquisition`，能力包含安全路径、格式、字节大小、尺寸、像素预算、哈希和元数据。未注册或不可用 Provider 默认 fail closed。

## Image Acquisition

本地图片采集支持 PNG、JPG、JPEG、WebP，完成：

- F11 项目内相对路径与 reparse/path escape 检查；
- 格式与 Magic Bytes 校验；
- 字节大小与像素预算校验；
- PNG/JPEG/WebP 尺寸头部读取；
- SHA-256、MIME、尺寸和像素数量元数据；
- `untrusted` 信任级别与可追溯 artifact 引用。

现有 Image Adapter 已复用尺寸解析和像素预算，保持既有 deterministic-only 语义。

## Network / Browser Security

RA7-B 不执行真实网络请求，但已提供后续 Web Provider 必须使用的安全边界：

- 仅允许配置的 scheme，默认 HTTPS；
- 拒绝 localhost、私有、loopback、link-local、metadata、reserved、multicast 和 unspecified 地址；
- 可注入 DNS resolver 并校验全部解析地址；
- 每个 redirect 重新执行 URL/DNS 校验，限制跳转次数并拒绝 scheme downgrade；
- 拒绝 URL credentials、敏感 query、下载和凭据访问；
- 定义 clean profile、无 Host cookies/passwords/extensions、隔离存储、禁下载和禁 popup 的 Browser Isolation Contract。

F12 的默认拒绝策略保持不变；RA7-B 没有绕过 NetworkPolicy 或 ExecutionBroker。

## Evidence

`reference_evidence_v1` 增加可选 Acquisition provenance：

- `acquisition_id`；
- `acquisition_status`；
- `request_fingerprint`；
- `provider_id` / `provider_version`；
- `snapshot_version`。

新增 `acquisition_manifest_v1.schema.json`，并在 `scripts/reference_protocol.py` 增加 Manifest 与 Evidence 扩展字段校验。

## Tests

- RA7-B 定向契约：`8 passed`；
- RA7-B + Reference R0–R6 + Security 相关回归：`91 passed`；
- 完整仓库回归：`698 passed, 5 skipped, 113 subtests passed`；
- RA7-B 相关 JSON Schema：解析通过；
- `git diff --check`：通过。

## Skipped

5 个既有 skip 全部来自 `tests/test_docker_execution_environment.py`，原因是 Windows Docker daemon/named pipe 不可用。没有新增 RA7-B skip，也没有用 skip 隐藏失败。

## Known Limitations

- 真实 Web/Browser Acquisition、DOM、Computed Style、截图采集仍未启用；当前 Web Adapter 继续只做 URL 安全规范化并保持 deferred。
- Codex Native Multimodal Perception Provider 和 project-local image 到模型的程序化注入仍未实现；本阶段只提供确定性图片 Acquisition 和未来 Provider 所需的 provenance 接口。
- Docker execution 环境仍受机器上的 Docker daemon 限制。

## Next Phase Readiness

RA7-C 可在本阶段协议和安全边界之上继续实现 Host-native Multimodal Perception Provider，但必须保持 image artifact hash、结构化 REFFND、`observed/inferred/unknown` 分层和 no-authority 约束。RA7-D Web Acquisition 仍须在 F11/F12 Browser 接线和隔离验证后实现。

## Gate Decision

CONTINUE

