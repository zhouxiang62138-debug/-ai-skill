# F10 迁移与回滚报告

## 兼容策略

v3-v6 项目仍能加载和校验，首次读取不修改文件。旧
`check/preview/migrate/verify/rollback` 入口保留 v6 行为；F10 新增：

```text
python scripts/project_migration.py runtime-preview <project.yaml>
python scripts/project_migration.py runtime-migrate <project.yaml> --backup <path>
python scripts/project_migration.py runtime-verify <project.yaml>
```

## v7 迁移

1. 只读预览 v7 投影。
2. 要求不存在的备份路径，先保存原 YAML。
3. 追加 `memory/migrations/migration-<nnn>.json`。
4. 初始化 `.runtime/sessions.sqlite3` 和 Session 创建 Event。
5. 原子写入 v7 `project.yaml.runtime` 投影。
6. 验证 YAML、Runtime 字段和迁移记录。

归档项目、活动 v3 来源链等高风险状态仍拒绝自动迁移。

## 回滚

现有 rollback 在恢复备份前保存当前 v7 文件，且不删除 SQLite Session DB；数据库保留
审计历史。回滚后旧 v3-v6 文件驱动流程继续可读。测试已证明 v4→v7→v4 的回滚路径。

## 已知限制

SQLite 与 YAML 不是跨介质原子事务；F10 用 pending revision、hash、幂等键和 Recovery
补偿，而不宣称不存在崩溃窗口。Windows 不采用 POSIX 目录 fsync 假设。
