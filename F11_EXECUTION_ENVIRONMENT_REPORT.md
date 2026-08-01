# F11 EXPERIMENTAL PROTOTYPE：执行环境报告

> F10R 范围更正：本文件描述的实现已移入 `experimental/f11_execution_environment/`，
> 不属于正式 Runtime，不提供 Docker 隔离或生产级执行安全边界；以下历史性描述不能作为
> 已交付能力或安全承诺。

已实现 `ExecutionEnvironment`、`LocalWorkspaceEnvironment` 和 Docker 可用性适配器。
本地环境只执行用户确认的白名单参数数组，强制 `shell=False`、项目内 cwd、路径/符号
链接逃逸检查、超时、输出上限与 Runtime Tool Call/Event 记录。Docker 不可用时明确阻塞，
不会回退到任意 Shell。

限制：本地白名单执行仍使用用户授权的宿主权限；生产隔离应优先使用 Docker 环境。
