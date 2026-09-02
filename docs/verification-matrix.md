# 全链路验证矩阵

ProjectOS 使用三张可追溯矩阵，而不是一套固定业务测试：

1. **Coverage Matrix**：`AC-* -> Architecture -> Project Contract -> implementation unit -> ChangeSet -> test/runtime evidence`。
2. **Quality Matrix**：由 Project Contract 的测试类型、入口和 runtime profile 生成适用维度；只记录项目声明的检查，不擅自增加业务要求。
3. **Recovery Matrix**：由 Trace 事件统计失败类型、重试次数、恢复结果和收敛率。

Coverage Matrix 持久化在 `.projectos/delivery/traceability.json`，质量维度持久化在
`.projectos/delivery/quality-matrix.json`。API 的 `/runs/{trace_id}/metrics` 会返回覆盖率、
质量维度和恢复指标，所有数据都从磁盘上的 Trace/Delivery 事实重建，服务重启不会丢失。

最低交付条件是：每个必须的 `AC-*` 都有实现单元、测试证据和运行证据；每个实现文件有唯一
owner 且 ChangeSet 已合并；必需控制面产物均已落盘；Review 结论为 `PASS` 或
`CONDITIONAL_PASS`。项目差异通过 Contract 的 `required_test_types`、接口、入口和验收
条件表达，系统底线只负责权限、路径、证据和状态一致性。
