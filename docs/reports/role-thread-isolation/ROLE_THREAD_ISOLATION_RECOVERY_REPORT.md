# Role Thread Isolation：Recovery Report

恢复只依赖 Durable Session、Event、Role Run、Role Execution、Model Invocation、
Context Manifest、Checkpoint、Tool Call 和 project revision。

Host crash 后，`STARTED` Role Execution 变为 `UNKNOWN_AFTER_CRASH`，仍为 ACTIVE 的
Model Invocation 变为失败，历史与业务 revision 保留。当前默认 Host 不支持 resume，
所以恢复后的下一次工作应创建新的 Role Execution，并由 Context Builder 重建上下文；
同一 Role Run 是否重启由上层 Recovery Policy 决定。

用户 pause 会取消活动 Role Execution、撤销 Lease，并保留 project state。重复恢复
通过幂等事件和终态检查，不删除记录、不依赖 Child Thread 聊天记忆。
