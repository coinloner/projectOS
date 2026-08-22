# Sandbox API 接口文档

## SandboxController

```python
SandboxController(
    policy: SandboxPolicy | None = None,
    provider: DockerSandboxProvider | None = None,
)

SandboxController.run_check(project_path: str, check_id: str) -> SandboxResult
```

Controller 加载 `runtime.yaml`，交给 `SandboxPolicy` 校验并生成固定 `SandboxSpec`，再委托 Docker Provider。manifest 缺失、profile/check 不允许、workspace 不存在或依赖缓存缺失时，返回 `SandboxStatus.SETUP_FAILED`，而不是执行宿主机回退。

## SandboxResult

```python
SandboxResult(
    status: SandboxStatus,
    check_id: str,
    runtime_profile: str | None,
    exit_code: int | None,
    duration_ms: int,
    stdout: str = "",
    stderr: str = "",
    message: str | None = None,
)

```

状态枚举：`passed`、`failed`、`setup_failed`、`timed_out`。输出会受策略长度限制。

通过 Test domain 的 `run_sandbox_check()` 调用时，结果会由 `TraceStore` 写为 `SandboxEvidence`，而不是仅由 LLM 写进 `tests.md`。证据还保存 `id`、`trace_id`、`work_item_id`、`agent_id` 与创建时间；Review 可在当前 Trace 内只读它。

## Dependency Resolver

```python
DockerDependencyResolver.resolve(
    project_path: str,
    *,
    approved: bool,
) -> DependencyResolutionResult
```

当 `approved=False` 时抛出 `PermissionError`。已批准的解析只挂载临时 `requirements.in` 和项目私有 wheel cache，不挂载 workspace；下载容器可以使用 bridge 网络，测试容器仍无网络。Resolver 会在下载容器中解析普通 `requirements.in`，成功后将解析结果固化到 cache 并标记为可离线使用。

Resolver 尚未连接到 GraphRunner 的批准/恢复状态机，Agent 不得自行调用它。
