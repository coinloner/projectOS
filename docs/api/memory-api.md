# Memory API

`MemoryStore(project_path)` 按 Trace 保存追加式 `MemoryEvent`：

```python
event = store.append(
    trace_id="tr-...",
    role="assistant",
    event_type="agent_output",
    content="已完成需求分析",
    work_item_id="wi-requirement",
)
view = store.view("tr-...", work_item_id="wi-requirement", limit=12)
prompt_context = view.as_prompt()
context = MemoryContextAssembler(store).build(
    trace_id="tr-...",
    work_item_id="wi-requirement",
    query="需求分析失败原因",
)
prompt_context = context.as_prompt()
```

`events()` 支持 `work_item_id`、`roles`、`after_sequence` 和有界 `limit` 过滤；`search()` 默认使用 SQLite/FTS5 做关键词
召回，并回到 JSONL 校验结果。构造 `MemoryStore(project_path, embedding_provider=...)` 后，search 会额外进行向量召回和
混合排序；provider 出错时回退到 FTS。`MemoryContextAssembler` 固定合并最近窗口和召回结果，避免所有相关事件进入 Prompt。
`checkpoint()` 将恢复参考状态作为控制事件保存。单条内容上限为 16000 字符。

长期候选使用：

```python
candidate = store.propose_durable(
    trace_id="tr-...",
    content="项目偏好：所有文档使用简体中文",
    source_refs=("mem-source-id",),
)
store.promote(candidate.id, trace_id="tr-...")
```

上层不应直接写入用户任意记忆，长期提升由控制面、Review 或人工确认完成。

`search_durable(query, limit=...)` 只检索当前项目内已经 `promote()` 的 durable 事件，适用于需要跨 Trace
复用已确认项目偏好的 Repair/规划流程。`MemoryContextAssembler.build(..., include_durable=True)` 才会把它们
加入 Prompt；默认值为 `False`。

Trace 结束时可以调用 `summarize_trace(trace_id, status=...)` 生成 episodic `run_summary`。摘要只引用原始事件，
同一 Trace 的旧摘要会被追加的 supersede 状态屏蔽。

HTTP 查询接口为：

`GET /api/v1/projects/{project_id}/runs/{trace_id}/memory`

可选参数：`work_item_id`、`query`、`after_sequence`、`limit`。提供 `query` 时使用 FTS 召回，否则返回顺序事件。
响应包含 `messages`、`latest_sequence` 和过滤条件。HTTP 查询默认使用 FTS；向量 provider 由项目运行时配置，
不会改变 API 契约。

长期记忆的控制面接口为：

- `GET /api/v1/projects/{project_id}/memory/candidates`：列出尚未审批的 durable candidate。
- `POST /api/v1/projects/{project_id}/memory/candidates/{event_id}/promote`：请求体提供 `trace_id`，显式批准候选。
- `POST /api/v1/projects/{project_id}/memory/candidates/{event_id}/expire`：请求体提供 `trace_id`，拒绝/过期候选。

批准和过期都追加控制事件，不修改原始候选；`durable_candidates()` 会自动排除已处理项。
`cleanup_expired()` 可由项目级维护任务周期调用，为设置了 `expires_at` 的事件追加幂等的过期记录。
