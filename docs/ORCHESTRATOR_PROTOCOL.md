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
python -m runtime.cli begin-step <project_root>
python -m runtime.cli inspect-step <session_id> --project-root <project_root>
python -m runtime.cli commit-step <session_id> --project-root <project_root> --run-id <id> --lease-token <token> --result <result.json>
python -m runtime.cli fail-step <session_id> --project-root <project_root> --run-id <id> --result <reason.json>
python -m runtime.cli resume <session_id> --project-root <project_root>
python -m runtime.cli pause <session_id> --project-root <project_root>
python -m runtime.cli recover <session_id> --project-root <project_root>
```

Codex 不由 Python 自动调用模型：先用 `begin-step` 取得角色、Run ID、Lease Token 和
expected revision，完成角色工作后把结构化结果交给 `commit-step`。结果必须包含
`source_status`、`target_status`、`changed_fields`、`expected_revision` 与
`idempotency_key`；完整 `next_state` 不被接受。暂停和恢复后都会撤销旧 Lease，恢复步骤
会签发新的 Lease Token。
