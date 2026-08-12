# F14 当前测试基线报告

报告阶段：F14-A Plan Hardening  
报告日期：2026-08-11  
用途：记录进入 F14-B Go/No-Go 判断所需的当前可复现测试证据。

## 1. 测试环境修复验证

Skill 仓库之外创建了专用目录：

    C:\Users\28388\Desktop\f14-a-temp-tests-20260811

测试进程显式设置：

    TEMP=C:\Users\28388\Desktop\f14-a-temp-tests-20260811
    TMP=C:\Users\28388\Desktop\f14-a-temp-tests-20260811
    TMPDIR=C:\Users\28388\Desktop\f14-a-temp-tests-20260811

独立执行 Python tempfile 验证：

- tempfile.gettempdir() 使用上述目录。
- tempfile.TemporaryDirectory() 创建成功。
- 显式 cleanup 后临时目录不存在。

结论：前一轮错误的直接根因是当前执行环境默认 TEMP 候选目录不可用；使用 Skill 仓库外显式可写临时目录后，Python 临时目录能力恢复。没有修改产品行为。

## 2. 重点回归集合

执行命令：

    python -m unittest -v tests.test_context_builder tests.test_context_budget tests.test_context_resume tests.test_formal_context_builder tests.test_context_rollover tests.test_role_thread_isolation tests.test_evaluator_independence tests.test_evaluation_governance tests.test_evaluation_evidence tests.test_evaluation_protocol

结果：

| 项目 | 结果 |
| --- | --- |
| 启动测试数 | 120 |
| 通过 | 120 |
| Failure | 0 |
| Error | 0 |
| 运行时间 | 1.332 秒 |
| 当前证据级别 | Current Run Verified |

## 3. 完整测试集合

执行命令：

    python -m unittest discover -s tests -p 'test*.py'

结果：

| 项目 | 结果 |
| --- | --- |
| 启动测试数 | 421 |
| 通过 | 421 |
| Failure | 0 |
| Error | 0 |
| 运行时间 | 26.975 秒 |
| 当前证据级别 | Current Run Verified |

## 4. 错误分类

| 分类 | 本次结果 |
| --- | --- |
| Environment Failure | 前一轮 29 个；已通过显式 TEMP/TMP/TMPDIR 复验 |
| Existing Product Failure | 0 |
| F14-related Failure | 0；本轮没有实现 F14-B 生产功能 |
| Unknown | 0 |

F13 历史报告仍标记为 Historical Test-Proven，不作为本次当前回归的替代证据。

## 5. 范围声明

本报告只证明当前仓库在显式可写临时目录下的重点和完整测试集合通过。它不证明任何尚未实现的 F14-B 组件已经存在，也不授权修改生产 Runtime。
