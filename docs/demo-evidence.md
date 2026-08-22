# Demo Evidence

仓库中的示例产物用于说明如何检查一次运行，而不是伪造端到端通过结果。

## `projects/Mega_Shit`

这是一个历史动态交付样例，已经包含：

- `requirement.md`
- `architecture.md`
- `tasks.md`
- `implementation.md`
- `tests.md`
- `review.md`

其中 `review.md` 明确记录了 BLOCKED 结论，证明 Review 阶段能够产出审查结果，即使交付不满足通过条件。

## `projects/todo_architecture_compact_demo`

这是 Todo 的架构/实现阶段样例，包含需求、架构、任务、环境、实现和测试产物。它没有被当作完整交付通过证据；要生成新的完整 Todo 交付，应按 README 的真实运行流程创建新项目，并确认 Docker preflight 为 `ready`，最终检查 `review.md`。

## 证据检查

```bash
find projects/<project>/.projectos/runs -maxdepth 2 -type f | sort
ls projects/<project>/{requirement,architecture,tasks,environment,implementation,tests,review}.md
```

只有 Trace 事件、SandboxEvidence 和 `review.md` 同时存在时，才可以把一次运行称为完成了交付审查。
