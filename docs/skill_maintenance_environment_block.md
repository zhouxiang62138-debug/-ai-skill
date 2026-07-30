# Skill Maintenance 环境阻塞与恢复

F8.5 的 Windows 符号链接集成验收需要在真实文件系统上分别创建目录符号链接和
文件符号链接。进程缺少该权限时，确定性原因代码为：

`windows_symlink_privilege_missing`

它属于验收环境阻塞（`acceptance_environment`），不是同步事务损坏。同步回滚无法
恢复安装副本时使用另一原因代码 `sync_rollback_failed`。两者不能互换，也不能共用
恢复逻辑。

## 进入阻塞

只能从正式验收状态 `EVALUATING`、角色 `evaluator` 进入该环境阻塞：

```powershell
python scripts/skill_maintenance.py block-symlink <独立项目目录>\project.yaml
```

命令通过原子状态写入把项目更新为：

```yaml
status: BLOCKED
next_role: null
f8_5_status: BLOCKED
blocked_reason: windows_symlink_privilege_missing
blocked_context:
  category: acceptance_environment
  resume_status: EVALUATING
  resume_next_role: evaluator
```

`next_role: null` 是 project v5 Schema 明确允许的值。

## 确定性解除阻塞

恢复操作不会直接产生 PASS。先执行真实能力探测：

```powershell
python scripts/skill_maintenance.py check-symlink-privilege
```

探测会在临时目录真实创建目录和文件符号链接。两种链接都成功后，才能执行：

```powershell
python scripts/skill_maintenance.py resume-symlink-block <独立项目目录>\project.yaml
```

恢复代码仅接受原因代码为 `windows_symlink_privilege_missing`、恢复上下文完整的
`BLOCKED` 状态。准确恢复状态固定为：

```yaml
status: EVALUATING
next_role: evaluator
f8_5_status: FAIL
final_evaluation_status: null
required_tests_status: null
```

随后必须依次运行两个真实符号链接测试、完整测试套件和最终 F8.5 Evaluation。
断言失败时进入 `FAIL` 并修复生产实现；仍出现 `WinError 1314` 时重新进入同一环境
`BLOCKED`；只有全部硬性验收通过，Evaluator 才能按正式验收状态转换决定 PASS。
