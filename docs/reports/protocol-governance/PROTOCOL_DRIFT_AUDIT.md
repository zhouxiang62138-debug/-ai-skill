# Protocol Drift Audit

报告日期：2026-08-12

本报告是本轮 F14 Global Production Rollout 的新增审计工件。它只记录当前 Skill 本体仓库的协议收敛结果，不改写历史 F14 报告，也不读取或创建 managed project 的 `project.yaml`。

## 结论

| 检查项 | 结果 |
| --- | --- |
| Protocol Drift | PASS |
| Authority Chain | PASS |
| Core Role 边界 | PASS：仅 Planner、Generator、Evaluator |
| Formal Module 边界 | PASS：First-Ask、Research、Reference、Change Request |
| Research Route | PASS：Intake → Requirement Research → Reference Analysis → Planning |
| Product / Plan 独立门禁 | PASS |
| Wait State 不启动 Role | PASS |
| F14 当前生产状态 | PASS：Controlled Qualified，默认仍为 F13 Full |
| Global Production Rollout | NOT ENABLED |

## 第一性原则审计

1. 单一权威：新增 `config/protocol_manifest.yaml`，集中声明协议版本、schema 版本、核心角色、正式 Module、运行时入口和研究路线。Markdown、Prompt、README 不再被当作并行 Truth。
2. 状态只能由正式 Runtime 路由：`runtime/policy.py:load_runtime_routes` 和 `runtime/role_selector.py:select_role` 是执行入口；等待用户状态必须带 `wait_for_user: true`，不能启动 Role。
3. Module 不是第四个 Agent：角色集合严格固定为 Planner、Generator、Evaluator；Research、Reference Analysis、First-Ask 和 Change Request 只作为 Module。
4. 研究不能被跳过：正式路径明确包含 `REQUIREMENT_RESEARCH`、`REFERENCE_ANALYSIS` 和回到 Intake 的闭环；是否执行由研究门禁判定，不能靠文档措辞隐式跳过。
5. Product Approval 与 Plan Approval 必须分离：产品评审前 `active_plan` 必须为空，Plan 评审前 `approved_plan` 必须为空，Generator 只能读取完整来源链。
6. 失败必须安全：F14 的资格不足、Kill Switch、项目/角色/阶段覆盖和优化异常均回到 F13；缓存层的 Secret 拒绝码统一为公开 Context 错误码 `CONTEXT_SECRET_FORBIDDEN`，消除同一语义的错误码漂移。
7. 历史版本只读迁移：v3-v6 被明确标记为 legacy/read-only compatibility，不再被当前文档当作可执行协议。

## 自动化证据

```text
python -B scripts/protocol_consistency.py check
Protocol consistency: PASS
Checks: 8

python -B -m unittest tests.test_protocol_consistency tests.test_f14_rollout_governance -v
Ran 12 tests ... OK

python -B -m unittest discover -s tests -p 'test*.py'
Ran 509 tests ... OK

python -B -m pytest -q -p no:cacheprovider
930 passed, 6 skipped, 129 subtests passed

git diff --check
PASS
```

pytest 使用了仓库外的临时目录；测试数据没有写入 Skill 目录。

## Authority 快照

- 基线提交：`e6131771f13a09ec80da5998c2a4ea9aa1fa6e5d`
- 当前分支：`codex/f10-runtime-correctness-hardening`
- 当前工作树：有未提交修改；本轮没有自动 commit、push 或 PR。
- `config/workflow.yaml`：`6C28EA2A7816C0BCE30CA9CC0D3DBE8D2FB9E709648D130C70B6BAC60BA63ACC`
- `config/role_policies.yaml`：`9D7951D816F5A41A83CD0D167B38E27766C26AABE8A88CB76C573156DCD94FE2`
- `config/requirements_discovery.yaml`：`A064E9D45298B7C2760385F1FE02B990C835D250544227A7F36EBFBDBEA475BC`
- `config/context.yaml`：`B172D2683B5C283E85A536AF79A5382A213674A793E5F1445D4FCC5C96E11B0B`
- `config/f14.yaml`：`C6D5B70AEA3A174214C05BD783CADC9139F97FD0DB6C5EE8D4104550033DC19E`
- `config/runtime.yaml`：`CBE61DCF27F4A18085B9C8403FD4F8F0F8278F3FFDC417D2AD5C452315D18995`
- `config/evaluation_independence.yaml`：`DB2E6B0D389D97E33FDE3FAFFF5B5CBFD4EC155CB160BB186E501D093445D91E`
- `config/protocol_manifest.yaml`：`FD77E8A2EA5A2B27A1CFEA6E3F7A9641C1A28E1919576DD16653E6FC03FEDD67`

## 审计边界

既有历史报告中的 controlled adapter、fixture bytes 和历史资格结论被保留为历史证据；它们不被升级成 Real Model 或 Real Browser 证据。本轮没有伪造 Host usage、token、浏览器视觉分数或 Gold Case C 生命周期。
