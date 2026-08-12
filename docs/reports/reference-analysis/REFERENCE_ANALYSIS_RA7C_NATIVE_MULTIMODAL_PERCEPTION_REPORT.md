# RA7-C Native Multimodal Perception 阶段报告

## 阶段

RA7-C — Native Image Perception Integration

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段完成了正式的 Native Multimodal Perception Provider 边界、项目本地图片证据输入校验、REFFND 结构化输出校验和 Perception Run 追加式审计记录。当前 Codex Host 没有向 Skill Runtime 暴露项目本地图片到多模态模型的程序化调用桥，因此生产 Provider 仍明确标记为 `UNAVAILABLE`，没有伪造视觉 Finding 或模型身份。

## 实现链路

```text
Image Evidence (artifact_ref + SHA-256)
  -> PerceptionRequest
  -> CodexNativeMultimodalPerceptionProvider
  -> bounded Host invocation adapter (when injected)
  -> REFFND validation
  -> PerceptionRun(result_hash + limitations)
  -> append-only perception-run YAML
```

实现位于 `runtime/reference_analysis/perception.py`，公共导出位于 `runtime/reference_analysis/__init__.py`，运行摘要 Schema 位于 `config/schemas/perception_run_v1.schema.json`。

## 能力状态

- Host multimodal input：AVAILABLE（Host 层能力，不等于 Skill Runtime 可编程调用）
- 项目本地图片到 Host 模型桥：NOT_IMPLEMENTED
- `CodexNativeMultimodalPerceptionProvider`：已实现协议边界和注入式 adapter 接口
- 默认 Provider availability：`UNAVAILABLE`
- model identity：`not_exposed`
- additional API key：不需要；本阶段没有读取凭据或调用外部 Vision API
- 生产图片语义感知：NOT_READY
- text/reference deterministic 流程：保持可用

## 输入与安全边界

- 只接受已注册 `REFEV-*`、同一 `REF-*`、项目内 `artifact_ref`、允许的图片 MIME 和 SHA-256。
- Provider 通过 `ExecutionPathPolicy` 读取图片，读取后再次计算 SHA-256，哈希不一致直接拒绝。
- 请求显式携带 domains、scope、exclusions、上下文预算和输出预算；超过预算直接失败。
- Host adapter 只能接收受控图片字节和任务范围，不能接收 `project.yaml`、Runtime 状态、审批、CAS、角色切换或其他权威字段。
- Provider 输出只允许 `findings` 与 `limitations`，每个 Finding 必须绑定同一 Reference 和输入证据 ID，并通过现有 `reference_finding_v1` 校验。
- 图片中的提示注入文本继续作为 `untrusted` Finding 数据处理，不会升级为工具、状态或工作流指令。

## 溯源与生命周期

- `PerceptionRun` 保存 provider/version、输入证据哈希、请求域、状态、结果哈希、限制项、模型身份和时间。
- `PerceptionRunStore` 使用 `artifacts/references/perception/perception-run-<id>.yaml` 追加快照；同一 `run_id` 已存在时拒绝覆盖。
- `UNAVAILABLE`、`SUCCEEDED` 和 `FAILED` 都可记录；输入哈希错误、输出结构错误和越权字段错误会留下 `FAILED` 摘要后重新抛出原始错误。
- `validate_perception_run` 与 JSON Schema 双重约束运行记录，运行记录不包含原始图片字节。

## 验证证据

- RA7-C 专项测试：`5 passed`
- RA7-B + RA7-C + Reference R0-R6 + Security 相关回归：`114 passed`
- 全量回归：`703 passed, 5 skipped, 113 subtests passed`
- `python -m py_compile`：通过
- `git diff --check`：通过

## 已知限制

1. 当前没有可由 Skill Runtime 调用的真实 Codex Native 多模态桥，因此没有生产图片语义 Finding。
2. 用户交互层附件身份到项目 Reference Evidence 的稳定映射尚未落地；本阶段使用项目内已注册图片 artifact 作为受控输入。
3. Browser/Web acquisition、截图、DOM、Computed Style、多视口、PDF、Repository、Video 和 R6 Visual Conformance 仍未实现。
4. Provider 目前不承担多模型路由、概率校准、自动产品决策或自动修改代码。

## Gate 决策

CONTINUE — RA7-C 的安全协议和不可用降级已具备可复现证据；生产 Host bridge 继续保持 `NOT_READY`，后续 RA7-D 只能在同样的 F11/F12/F13、哈希和隔离约束下接入 Browser Acquisition。
