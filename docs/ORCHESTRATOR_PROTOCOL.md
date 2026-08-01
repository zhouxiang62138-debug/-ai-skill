# Orchestrator 协议

## 职责

1. 定位项目和 Session DB。
2. 读取并验证 project schema。
3. 获取、续约、释放或接管过期 Worker Lease。
4. 根据 `active_module`、`status`、`next_role` 确定性选择目标。
5. 记录角色/工具生命周期 Event。
6. 通过 CAS 提交角色产生的候选业务状态。
7. 创建 Checkpoint 并执行恢复。

## 禁止事项

Orchestrator 不得生成产品内容、编写业务代码、修改需求、判断 PASS/FAIL、越过
用户批准或引入新 Agent。WAIT 状态只返回等待原因。

## CLI

```text
python -m runtime.cli start <project_root>
python -m runtime.cli resume <session_id> --project-root <project_root>
python -m runtime.cli inspect <session_id> --project-root <project_root>
python -m runtime.cli pause <session_id> --project-root <project_root>
python -m runtime.cli recover <session_id> --project-root <project_root>
```
