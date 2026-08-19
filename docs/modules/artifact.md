# Artifact 模块

`app.artifact` 负责项目 Markdown 产物的存储边界。目前有两套互补能力：

- `ArtifactStore`：原有固定根目录文档的受限读写，例如 `architecture.md`。
- `ArtifactRepository`：供可并行的架构 Markdown 试点使用的暂存、候选和发布版本链路。

## 架构 Markdown 试点

```text
分区 WorkItem
  -> .projectos/runs/<trace_id>/work-items/<work_item_id>/output/<slot>.md
  -> Integration WorkItem 创建 candidate
  -> Quality Gate 确定性检查并 promote
  -> .projectos/artifacts/architecture/revisions/rev-XXX.md
  -> architecture.md（兼容投影）
```

`StagedArtifact` 只属于创建它的 `trace_id/work_item_id/slot`。集成节点只能读取
WorkItem 预先写入 `input_refs` 的引用；它不能拼接路径或发现其他运行的暂存文件。

`ArtifactCandidate` 包含正文摘要、来源引用和 `IntegrationReport`。当前报告先做
确定性底线检查：所有来源必须是同一 artifact 的可读取 staged output，且至少存在一个
来源。发现问题时状态为 `needs_rework`；`ArtifactRepository.promote_candidate()` 会拒绝发布。

质量门通过时才会追加一个不可变的 revision，并更新 `current.json` 和根目录
`architecture.md`。因此旧的单节点 Agent 仍可读取根目录文档，而并行节点从未拥有其写权限。

## Code Git 交接

代码文件不使用 Markdown candidate，而使用 Git 的资源级 ChangeSet：

```text
PARTITIONED CodeAgent
  -> .projectos/worktrees/<trace>/<work-item>/workspace/<slot>/...
  -> projectos/<trace>/<work-item> branch commit
  -> .projectos/runs/<trace>/git/tasks/<work-item>.json
  -> Integration worktree 三方 merge
  -> 控制面发布 workspace/ 变化
```

每个 Trace 的 baseline 保存在 `.projectos/runs/<trace>/git/baseline.json`。ChangeSet
保存 `base_commit`、分支、提交、完整变更文件和 worktree 路径。Code Policy 只检查
ChangeSet 是否齐全、是否共享 baseline、是否越过 backend/frontend 分区；代码语义和
可运行性仍由 TestAgent 的 Docker 证据与 Review 负责。

当前冲突会以结构化错误阻止发布，不会自动选择任一分支，也没有把任意 Git 命令暴露给
IntegrationAgent。下一步再增加受限的冲突读取、解决提交和失败后的局部重规划。

当前 Architecture Workflow 对 staged scope 和候选正文施加字符上限，避免多个 Agent
反复生成完整架构文档。它保证输出规模，不保证语义正确性；例如 scope creep、接口字段
不一致和未支持的能力仍需要后续质量策略检查。
