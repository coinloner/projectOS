## 2026-08-21

- 明确运行时只有两条路径：受控 Workflow，或动态 Planner 生成 DAG。
- 移除未参与正式执行路径的 `requirement_generation` 模板，保留 `requirement_agent`。
- 增加能力审批查询和批准后恢复接口：`GET .../capabilities`、`POST .../capabilities/approve`。
- 增加 Docker runtime preflight，提前报告 `python:3.12-slim` 镜像和依赖缓存前置。
- Sandbox setup failure 会保留证据并继续进入 Review，由 Review 产出 BLOCKED/CONDITIONAL_PASS，
  不再让整个交付链停在测试节点。
- CLI 改为显式 `--project --goal [--workflow]`，移除硬编码项目入口。
