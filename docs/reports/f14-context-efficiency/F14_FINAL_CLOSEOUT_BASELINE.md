# F14 Final Closeout Baseline

> 本文件记录本次最终收尾开始前的事实基线，不改写历史报告。

记录日期：2026-08-12
仓库：`C:\Users\28388\Desktop\ai-development-team-skill`
分支：`codex/f10-runtime-correctness-hardening`

## Git 基线

- HEAD commit：`e6131771f13a09ec80da5998c2a4ea9aa1fa6e5d`
- HEAD tree：`e9c2ac7557f43a45eddade341e1b9067d813ec52`
- 工作区：`MODIFIED_NO_COMMIT`
- 工作树内容指纹：没有标准单一 Git SHA；本轮以 commit、tree、状态清单和下列文件 SHA-256 共同冻结。

其中，HEAD commit 是 Git commit object，HEAD tree 是该 commit 对应的 tree object；工作树指纹不是 tree SHA。

当前工作区已有修改和未跟踪文件，属于收尾开始前状态，未在本文件生成前改动。

## Authority 文件 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| `config/protocol_manifest.yaml` | `FD77E8A2EA5A2B27A1CFEA6E3F7A9641C1A28E1919576DD16653E6FC03FEDD67` |
| `config/workflow.yaml` | `6C28EA2A7816C0BCE30CA9CC0D3DBE8D2FB9E709648D130C70B6BAC60BA63ACC` |
| `config/role_policies.yaml` | `9D7951D816F5A41A83CD0D167B38E27766C26AABE8A88CB76C573156DCD94FE2` |
| `config/requirements_discovery.yaml` | `A064E9D45298B7C2760385F1FE02B990C835D250544227A7F36EBFBDBEA475BC` |
| `config/context.yaml` | `B172D2683B5C283E85A536AF79A5382A213674A793E5F1445D4FCC5C96E11B0B` |
| `config/f14.yaml` | `C6D5B70AEA3A174214C05BD783CADC9139F97FD0DB6C5EE8D4104550033DC19E` |
| `config/runtime.yaml` | `CBE61DCF27F4A18085B9C8403FD4F8F0F8278F3FFDC417D2AD5C452315D18995` |
| `config/evaluation_independence.yaml` | `DB2E6B0D389D97E33FDE3FAFFF5B5CBFD4EC155CB160BB186E501D093445D91E` |
| `config/schemas/project_v7.schema.json` | `AB47EBA5C0B4A2182583AD249B5016843623A2F7ACA8FBFE9640FC1AF21681FF` |
| `templates/project.yaml` | `A8559051704AF941064AC583E1EE443FC3F8F0068A308ADF565E091C2DF22E1F` |
| `config/retry_governance.yaml` | `63409A73673CE25EC6499E66C6C05C5AA597EC62EDAE9DEE8694E6B0C4C3B4A7` |
| `scripts/protocol_consistency.py` | `6E341458F6010E30FB3E8C442BC70F502409816360012E1463F2673D3F885F81` |
| `runtime/role_selector.py` | `4BA34750831A109DAC950521F5C358423840DDA633DE105D05EF0BEEFEFCAA76` |

## 测试基线

| 验证 | 结果 |
| --- | --- |
| `python -B scripts/protocol_consistency.py check` | PASS，8 checks |
| `python -B -m unittest tests.test_protocol_consistency tests.test_f14_rollout_governance -v` | PASS，12/12 |
| `python -B -m unittest discover -s tests -p "test*.py"` | PASS，509/509 |
| `python -B -m pytest -q -p no:cacheprovider` | 本轮提升权限运行超时；附件提供的既有基线为 930 passed、6 skipped、129 subtests passed |
| `git diff --check` | PASS |

## 收尾前已知状态

- Protocol Drift：PASS
- F14 Engineering：PASS
- F13 Emergency Fallback：READY
- F14 qualification：`CONTROLLED_QUALIFIED`
- 默认 Context：`f13_full`
- Global：NOT ENABLED
- 外部 Real Gold 能力：当前不可直接证明，禁止以 controlled adapter、fixture 或估算 token 代替。
