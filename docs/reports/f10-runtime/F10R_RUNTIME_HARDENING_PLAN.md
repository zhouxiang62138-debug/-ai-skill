# F10R Runtime Correctness Hardening Plan

本计划以 `F10R_RUNTIME_HARDENING_AUDIT.md` 为唯一审计输入，按阶段门禁执行。每阶段均须先新增/更新针对性测试、运行完整回归、记录证据，再进入下一阶段。

1. 将 Session Control Plane 移至 `Path.home() / '.ai-development-team' / 'runtime' / <project-id>`，实现显式预览、备份、迁移、验证和回滚；v7 缺失 Store 必须 BLOCKED。
2. 引入随机 Worker ID、只存 hash 的 Lease Token、heartbeat、上下文托管与最终写入前 fencing 校验。
3. 以受控 Commit Coordinator 取代公开布尔 CAS 绕过：先做幂等查询，再做 revision、权限、字段 diff 与状态迁移校验。
4. 将 YAML runtime 投影收缩为 `session_id`、`control_plane_id`、`revision`，并提供 v7 draft 显式迁移。
5. 实现 Tool Call 状态机、不可覆盖的 Control Plane 结果文件、SHA-256 与同事务 Event 记录。
6. 实现可重放 Recovery：工具、状态 revision 与 Evaluation 事务分别恢复并使用稳定事实键。
7. 将 Migration/Rollback 事务化，并实现项目路径的 inspect/preview/apply/verify/rollback rebind。
8. 完成 begin-step/commit-step/fail-step/pause/resume/recover CLI 协议，所有配置由 YAML 加载并验证一致性。
9. 更新 schema、文档和 F11–F13 的实验性表述，运行完整验收，生成所有 F10R 报告。

停止条件：任何阶段不能证明隔离、fencing、不可变结果、缺失 DB 阻止或迁移恢复，则立即标记 BLOCKED，不降低标准。
