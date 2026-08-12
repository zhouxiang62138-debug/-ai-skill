# F9.2 可复现验收阶段报告

## 1. 实施摘要

阶段结果：`PASS`。

新增 Evidence Manifest、七级验收 Gate、安全命令执行、Requirement/Issue 证据映射、
受保护工件 SHA-256 快照和证据缺失确定性路由。语言模型声明不再能代替实际命令、
退出码、日志和时间证据。

## 2. Evidence 架构

每轮证据位于 `evaluation/evidence/evaluation-<nnn>/`。`manifest.yaml` 记录：

- 非敏感环境；
- 参数数组命令、起止时间、退出码和执行者；
- stdout/stderr 路径及截断状态；
- 证据工件；
- Requirement、Acceptance Criterion 与 Issue 映射；
- Gate 顺序和结果。

Manifest 追加写入，拒绝覆盖和未知交叉引用。

## 3. Gate 架构

固定顺序：

1. `GATE-DELIVERY`
2. `GATE-BUILD`
3. `GATE-TESTS`
4. `GATE-REQUIREMENTS`
5. `GATE-REGRESSION`
6. `GATE-NON_FUNCTIONAL`
7. `GATE-EVIDENCE`

是否必需由 evaluation profile 决定。必需 Gate 未执行、无证据或被跳过均不能 PASS。

## 4. 新增文件

- `scripts/evaluation_evidence.py`
- `config/evaluation_gates.yaml`
- `config/schemas/evidence_manifest_v1.schema.json`
- `templates/evidence_manifest.yaml`
- `tests/test_evaluation_evidence.py`
- `F9_2_REPRODUCIBLE_EVALUATION_REPORT.md`

## 5. 修改文件

- `config/workflow.yaml`
- `config/role_policies.yaml`
- `config/evaluation_rules/default.yaml`
- `config/evaluation_rules/web_app.yaml`
- `prompts/evaluator_prompt.md`
- `prompts/generator_prompt.md`
- `docs/F9_EVALUATION_PROTOCOL.md`

## 6. Schema 变化

新增 Evidence Manifest v1。Schema 与确定性 Python 校验共同保证唯一 ID、路径安全、
Gate 顺序、命令结果、必需证据和交叉引用一致性。

## 7. evaluation profile 变化

两个 Profile 均升级为 `schema_version: 2`，原总分 `8.0` 和各维度阈值保持不变。
新增硬 blocker/critical 上限、Gate 必需性、命令白名单、最小强制回归集和受保护
路径。没有降低任何验收标准。

## 8. 命令安全机制

- `shell=false`；
- 命令参数数组；
- Profile/已批准来源白名单；
- cwd 必须位于项目根内；
- 超时和输出字节上限；
- shell 与破坏性命令拒绝；
- stdout/stderr 敏感字段脱敏；
- 命令不存在与测试失败分别记录为 `BLOCKED` 和 `FAILED`；
- 日志追加创建，拒绝覆盖。

## 9. 测试结果

局部命令：

```text
python -m unittest -v tests.test_evaluation_evidence
```

结果：35 通过，0 失败，0 跳过。

完整回归：

```text
python -m unittest discover -s tests -v
```

结果：231 通过，0 失败，0 跳过。

## 10. 兼容性结果

- 旧 `evidence_v1`、`evidence_v2` 测试继续通过；
- 旧项目无 Manifest 时可继续读取历史验收，下一轮才生成新 Manifest；
- Gate 必需性按 Profile 决定，不给旧项目擅自增加未配置的非功能标准；
- 不依赖 Git，归档或无 Git 项目仍可做 Hash 检查。

## 11. 权限与受保护文件检查

Evaluator 仍不能修改代码、正式计划或 Profile。Generator 仍不能修改
`config/evaluation_rules/`。受保护快照检测修改、缺失和新增；正式计划、规格、
批准记录、Profile、Schema 和测试发生变化时创建 `unauthorized_change` blocker。

## 12. 已知限制

- 本阶段只实现确定性基础回归 Gate；重复失败、趋势和提前升级在 F9.3 实现。
- Profile 白名单使用逻辑命令名；运行环境的实际可执行路径由调用方安全解析。

## 13. 阶段完成标准逐项检查

- Manifest Schema 与交叉引用：PASS
- 命令、stdout/stderr、退出码和时间：PASS
- Gate 固定顺序与必需条件：PASS
- Requirement/Issue 映射：PASS
- 必需证据缺失阻止 PASS：PASS
- 受保护工件无 Git 检测：PASS
- 安全命令白名单、cwd、超时和脱敏：PASS
- 追加式证据与不覆盖历史：PASS
- 局部测试和完整回归：PASS
- 文档、模板和兼容说明：PASS

## 14. 阶段审核结论

`PASS`

## 15. 是否允许进入 F9.3

允许，自动进入 F9.3。

## 16. 工作副本与安装副本差异

安装副本未修改，仍落后于工作副本。最终系统审核 PASS 前禁止同步；届时必须重新
检查安装副本独立变化、创建可验证备份并执行同步后验证。
