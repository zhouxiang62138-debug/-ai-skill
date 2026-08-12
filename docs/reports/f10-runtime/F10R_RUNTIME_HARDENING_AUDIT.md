# F10R Durable Session Runtime Hardening Audit

日期：2026-08-01  
基线提交：`9005ea0`（`codex/f10-durable-session-runtime`）

## 基线

- 工作区：干净（创建返工分支前）。
- 运行命令：`python -m pytest -q`
- 结果：369 passed，104 subtests passed，退出码 0。
- `project.yaml`：本仓库根目录不存在；这是 Skill 源仓库而非受管项目，不能把它当作 v7 项目状态来源。

## 已验证发现

| # | 结论 | 证据 |
|---|---|---|
| 1 | FAIL | `config/runtime.yaml` 与 `runtime/recovery.py` 将 DB 固定为 `<project_root>/.runtime/sessions.sqlite3`。 |
| 2 | FAIL | Session Store、工具输出与 `.runtime` 都位于项目目录，Generator 可写项目目录。 |
| 3 | FAIL | CLI 默认 `worker-main`，见 `runtime/cli.py`。 |
| 4-6 | FAIL | `leases` 表没有 token/hash；提交栅栏未覆盖最终替换。 |
| 7 | FAIL | `runtime_authorized` 仍是公开布尔绕过路径。 |
| 8 | FAIL | CAS 在 revision 冲突检查后才处理既有幂等提交。 |
| 9-10 | FAIL | CAS 接受完整 next-state，未依据配置执行字段归属与合法状态迁移校验。 |
| 11 | FAIL | `runtime` 同时保存 event/checkpoint 指针，不能与 SQLite 原子同步。 |
| 12-15 | FAIL | Tool Call 只有 REQUESTED/COMPLETED，键只覆盖请求；输出可覆盖、无结果 hash，超时模型不完整。 |
| 16-18 | FAIL | Recovery 仅列出完成工具；恢复键依赖 revision；Evaluation 的恢复状态主要依赖注入点。 |
| 19-20 | FAIL | Migration 提前记录成功，Rollback 未将新 Session 标为 detached。 |
| 21 | FAIL | v7 缺 DB 会由 Store 构造器静默创建。 |
| 22 | PARTIAL | DB 存在且 project 缺失能报错，但 CLI 不能形成受控恢复路径。 |
| 23 | FAIL | Session 只保存绝对 `project_root`，无显式 rebind 协议。 |
| 24 | FAIL | `config/runtime.yaml` 未被 Runtime 作为运行时配置加载。 |
| 25 | FAIL | `role_selector.py` 与 `workflow.yaml` 存在重复路由事实。 |
| 26 | FAIL | F11–F13 报告标题/措辞未统一标为 EXPERIMENTAL。 |
| 27 | FAIL | `SKILL.md` 对新项目 schema 版本的陈述与 v7 Runtime 不一致。 |
| 28 | FAIL | Planner/Generator/Evaluator 的状态写入尚未统一接入受控 CAS。 |

## 结论

**F10：FAIL，需要返工。** 以上结论均须由后续新增的针对性测试复现和关闭；在所有必需测试通过前，不得把 F10R 判为 PASS。
