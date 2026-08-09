# F11.3A DockerExecutionEnvironment

## 实现内容

- 新增真实 Docker CLI Backend，使用参数数组执行 `create`、`start`、`exec`、`kill/stop` 和 `rm`。
- 仅挂载当前 execution workspace；`.runtime` 使用 tmpfs 隐藏，`project.yaml`（存在时）以只读方式挂载。
- 默认启用 `network=none`、非 privileged、私有 PID/IPC、`no-new-privileges`、`cap-drop=ALL`、资源限制和最小 Docker CLI 环境。
- 根据镜像 ID、profile、网络、资源和挂载策略计算真实 `environment_hash`。

## 测试结果

- Docker 集成测试：1 passed，5 skipped。
- 跳过原因：`docker info --format={{.ServerVersion}}` 无法连接 Docker Desktop daemon，Windows named pipe 不存在。
- 全量测试：431 passed，5 skipped，104 subtests passed。

## 已知限制

- 当前宿主 Docker daemon 不可用，因此真实 Container 创建、执行和删除尚未完成验收。
- Snapshot/Restore、Container crash recovery、Network Proxy 和 Credential Broker 留待后续阶段。

## 下一阶段入口

Docker Desktop daemon 可用后，重新运行 `tests/test_docker_execution_environment.py` 完成 F11.3A 验收；之后再进入 F11.3B / F11.4。
