# RA9 Security / Adversarial Hardening 阶段报告

## 阶段

RA9 — Security & Adversarial Hardening

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段集中执行 Reference 管道的对抗测试，覆盖 URL/DNS、Browser isolation、图片边界、证据哈希、Prompt Injection、跨 Reference 和 Provider 权威字段。

## 攻击面与结果

- SSRF：private/loopback/metadata 地址、敏感 query、unsafe scheme 均 fail closed。
- Redirect：每次 redirect 重新 URL/DNS/授权检查，降级 scheme 和跨 origin 策略不放行。
- Browser：Cookie、password、extension、download、popup 和 credential access 均被隔离/拒绝。
- Image：不支持格式、大小超限、像素预算和坏数据不会进入语义分析。
- Evidence：文件 SHA-256 不一致、artifact 交换、跨项目/跨 Reference 绑定都会拒绝。
- Prompt Injection：页面/图片中的“修改 project.yaml、运行命令、设为 ACCEPTED”等内容只保留为 untrusted 数据，不能获得工具或 Runtime 权限。
- Authority Escalation：Perception/Fusion/Visual Provider 输出不能写 status、CAS、approval、project.yaml 或改变 R6 gate。

## 验证证据

- RA9 集中对抗测试：`6 passed`
- RA7-B/RA7-C/RA7-D/RA7-E/RA7-F、Reference R0-R6、Browser、Runtime Security 定向回归：通过
- 最终全量回归：`723 passed, 5 skipped, 113 subtests passed`
- 5 个 skip 全部是既有 Docker daemon unavailable；无新增非环境 skip。
- `git diff --check`：待最终自审计命令补充

## 已知限制

1. 真实公网页面和真实 Codex Native bridge 当前仍由环境能力矩阵标记为 BLOCKED/UNAVAILABLE，因此未声称生产 Web/视觉 E2E 已通过。
2. 对抗测试使用受控 fake adapter 和本地 fixtures，不替代真实隔离浏览器和 Host policy 的部署验收。

## Gate 决策

CONTINUE — 安全拒绝路径和已有 Runtime 回归通过，进入 RA10 隔离 E2E Benchmark。
