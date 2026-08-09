# F10 测试报告

## 环境

- 平台：Windows
- Python：`C:\Users\28388\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
- 测试需要可写系统临时目录；只读沙箱会使 `tempfile` 夹具失败，这属于环境限制。

## 实际命令

```powershell
& 'C:\Users\28388\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

- 退出码：0
- 运行：366
- 通过：366
- 失败：0
- 错误：0
- 跳过：0
- 耗时：15.948 秒

## 覆盖证据

- Session、Event append-only、sequence、idempotency：`test_session_store.py`、
  `test_event_*.py`。
- Lease、过期接管、旧 Worker 栅栏：`test_worker_lease.py`、
  `test_expired_lease_recovery.py`。
- CAS 和直接写入拦截：`test_project_revision_cas.py`。
- 崩溃窗口、Tool Call、重复恢复：`test_crash_*.py`、
  `test_session_recovery.py`、`test_recovery_is_idempotent.py`。
- v3-v6→v7 preview、迁移和回滚：`test_schema_v*_to_v7_preview.py`、
  `test_migration_rollback.py`。
- 全量回归覆盖既有审批、探索、评价、变更请求和安装同步测试。
