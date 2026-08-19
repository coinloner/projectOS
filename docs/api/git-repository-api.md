# Git Repository API

`GitRepositoryManager` 是 ProjectOS 控制面使用的 Git 适配层。Agent 不直接获得 Git
命令；Agent 的文件修改仍然通过领域 Tool 完成。

## 基线和任务分支

```python
manager = GitRepositoryManager(project_path)
manager.initialize()
base_commit = manager.freeze_baseline(("workspace",))

task = manager.create_task_worktree(
    trace_id="tr-001",
    work_item_id="backend-create",
    base_commit=base_commit,
)
change_set = manager.commit_task(
    task,
    owned_paths=("workspace/backend/features/create.py",),
    message="projectos: backend create",
)
```

`commit_task()` 会读取 worktree 的变更列表，并拒绝超出 `owned_paths` 的文件。提交结果
包含基线 commit、任务分支、head commit 和变更文件列表。

## 三方合并

```python
integration = manager.create_integration_worktree(
    trace_id="tr-001",
    base_commit=base_commit,
)
result = manager.merge_task(integration, change_set)
```

合并使用 Git 的共同祖先、Integration 当前分支和任务分支执行三方合并。自动合并成功时
创建 merge commit；发生冲突时返回 `MergeConflict`，不会吞掉冲突或擅自选择一方内容。

```python
if not result.merged:
    # Integration 层读取冲突文件和三方内容后解决
    commit = manager.commit_resolved_merge(
        integration,
        message="projectos: resolve merge conflicts",
    )
```

## Code domain 接入

Code domain 已通过 `GitCodeStagingService` 接入该控制面：

```text
write_staged_code_file
  -> GitCodeStagingService
  -> task worktree + commit_task()
  -> .projectos/runs/<trace>/git/tasks/<work-item>.json
```

`CodeIntegrationService` 从 `ExecutionContext.input_refs` 加载 ChangeSet，先执行
`GitCodeIntegrationPolicy`，再按 WorkItem ID 排序调用 `merge_task()`。成功后调用
`promote_worktree_changes()`，只把 `workspace/` 下的提交差异复制到正式 workspace；
`implementation.md` 会记录 baseline、task commits、merge commit 和发布文件。

当前 Code 并行交付链路已经完全使用 Git worktree 和 ChangeSet，不再保留旧的 staged
repository。冲突目前阻止发布并返回文件列表，后续再接入 Integration Agent 的受限冲突
解决流程。
