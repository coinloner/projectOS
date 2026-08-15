# Domain API 接口文档

所有领域模块位于 `app.domain.<domain>`，均遵循 `Service + ToolSet + register_<domain>_tools()` 的结构。Service 是可独立测试的本地能力；ToolSet 是 CrewAI 工具适配器；注册函数把 ToolSet 交给 `ToolGateway`。

## 统一注册入口

```python
register_<domain>_tools(gateway: ToolGateway, project_path: str) -> None
```

启动时由组合根调用各 domain 的注册函数。领域 Agent 不自行注册工具，也不直接创建 Service。

## Requirement

```python
RequirementDocument(content: str)
RequirementDocument.empty() -> RequirementDocument
RequirementService(project_path: str)
RequirementService.save(document: RequirementDocument) -> None
RequirementService.load() -> RequirementDocument
RequirementService.update(document: RequirementDocument) -> None
```

`RequirementToolSet` 暴露 `save_requirement(content)`、`load_requirement()`，写入或读取 `{project_path}/requirement.md`。

## 其余 Domain ToolSet 合同

| Domain | 对 Agent 暴露的工具 |
|---|---|
| architecture | `load_artifact(artifact)`、`save_architecture(content)` |
| task | `load_artifact(artifact)`、`save_tasks(content)` |
| bootstrap | `load_artifact(artifact)`、`configure_runtime(profile, dependencies="")`、`inspect_runtime()`、`save_environment(content)` |
| code | `load_artifact(artifact)`、`save_implementation(content)`、`list_workspace_files()`、`read_workspace_file(path)`、`write_workspace_file(path, content)`、`inspect_runtime()` |
| test | `load_artifact(artifact)`、`save_tests(content)`、`list_workspace_files()`、`read_workspace_file(path)`、`write_test_file(path, content)`、`run_sandbox_check()` |
| review | `load_artifact(artifact)`、`save_review(content)`、`list_workspace_files()`、`read_workspace_file(path)`、`inspect_runtime()` |

### 领域限制

- `load_artifact()` 只能读取构造时配置的 artifact key；越权 key 返回错误文本。
- `write_workspace_file()` 只能在 `workspace/` 内写入允许的文本文件。
- `write_test_file()` 只能写 `workspace/tests/`。
- `run_sandbox_check()` 不接收命令参数，固定请求 `SandboxController.run_check(project_path, "unit")`。
- `configure_runtime()` 只接受 RuntimeCatalog 中的 profile；依赖内容写为 `requirements.in`，但不触发安装。

领域 ToolSet 的返回值统一是文本，便于 CrewAI 将工具结果放回当前推理循环。Service 仍可抛出明确的 I/O 或参数异常；ToolSet/底层受限存储会把可恢复错误转为文本结果。
