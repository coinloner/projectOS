# 端口生命周期

ProjectOS 不把端口当作一次性整数，而是把动态 host 端口建模为租约。

## 生命周期

```text
acquire → bind probe → Docker start → renew/status → release
                                  ↘ process exit / TTL → reclaim
```

`PortLifecycleManager` 位于 [app/runtime/port_lifecycle.py](/Users/coinloner/projectOS/app/runtime/port_lifecycle.py)。
租约包含 `lease_id`、owner、PID、端口列表、创建时间和过期时间。默认应用运行按项目保存到：

```text
.projectos/runtime/port-leases.json
```

同一项目在 API 重启或 Worker 重启后仍能识别旧租约；发现 PID 已退出或 TTL 到期时会自动回收。
端口本身仍由 `PortAllocator` 做真实 bind 检查，租约只负责跨进程协调，不能替代操作系统冲突检测。

## 探测模式

- `auto`（默认）：在受限沙盒无法执行 bind 探测时，允许租约继续申请，由 Docker 实际启动结果做最终确认。
- `strict`：探测权限不足直接拒绝，适合对端口冲突要求更高的部署。

通过 `PROJECTOS_PORT_PROBE_MODE=strict` 开启严格模式。应用启动失败、状态探测发现容器退出、显式
停止和 Runner shutdown 都会释放租约；释放操作幂等。
# 开发与生产运行模式

`runtime.yaml` 可选 `mode: development`。受信 FastAPI 运行器在该模式下为 Uvicorn
注入 `--reload`，生产模式保持无 reload。两种模式都经过 PortLifecycleManager 分配端口、
健康检查、停止进程/容器并释放租约；默认模式仍为 `production`，保持旧项目兼容。
