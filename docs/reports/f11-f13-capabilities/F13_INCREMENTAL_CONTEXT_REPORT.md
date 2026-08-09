# F13.3 Incremental / Resume Context Report

## Resume

- Durable manifest：复用 F10 `SessionStore`，只保存 Context 元数据、来源引用和 hash，不保存正文。
- Full / Incremental：无可用前序 Manifest、Manifest 无效或 Policy/Budget 变化时执行 `FULL_BUILD`；兼容前序时执行 `INCREMENTAL`。
- Revision-aware：记录基础 revision；前序 revision 高于当前 revision 时稳定拒绝。
- Policy-aware：每次 Resume 先重新执行当前 Capability、Path、Session、Role Run 和 Secret 检查，再比较 Context Policy/Budget 指纹。

## Delta

- 按规范化 source reference 与 content hash 确定性分类 `UNCHANGED`、`ADDED`、`MODIFIED`、`REMOVED`。
- Delta 输出排序稳定；当前完整 Context hash 与 Full/Incremental 模式无关。

## Recovery

- 支持新 Role Run、暂停后恢复和进程崩溃后的 durable Manifest 恢复。
- 不完整或损坏的前序 Manifest 不被信任，回退为 Full Build；不复用跨 Session、跨 Project 或跨 Role 的 Context。

## Security / Audit

- Resume 复用 F10 Event 体系，写入 `CONTEXT_RESUMED` 安全元数据事件。
- Manifest、Event 和错误路径不写入 Context 正文、Secret 或大 Payload。
- 当前实现不引入模型记忆、Token Cache 或第二套日志。

## Tests

- F13.3 targeted：34 passed。
- Full regression：505 passed, 5 skipped, 104 subtests passed。
- Skipped：Docker daemon 不可用，属于 F11 Docker Sandbox deferred；不计入 F13.3 PASS 证据。

## Verdict

`F13.3 RESULT: PASS`
