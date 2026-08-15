# ProjectOS System Map

本页用于快速定位模块。理解 ProjectOS 时，先按“控制、执行、项目、隔离”四层阅读，而不是逐个阅读类。

## 四层模型

| 层 | 关键模块 | 它回答的问题 |
|---|---|---|
| 控制层 | `app/planner/`、`app/workflow/` | 下一步由谁执行，何时结束或暂停？ |
| 执行层 | `app/agent/`、`app/domain/`、`app/tool_manager/`、`app/tool_catalog/` | 某个 Agent 能做哪些受限动作？ |
| 项目层 | `app/project/`、`app/artifact/`、`app/workspace/`、`app/runtime/` | 产物、源代码与运行时声明保存在哪里？ |
| 隔离层 | `app/sandbox/` | 生成项目如何在不接触宿主机权限的前提下被测试？ |

## 模块依赖方向

```text
Planner -> ExecutionPlan -> GraphRunner -> AgentRegistry -> Domain Agent
                                                   -> ToolGateway -> ToolCatalog / AccessPolicy
Domain Agent -> Domain ToolSet -> Domain Service -> ArtifactStore / WorkspaceToolSet / SandboxController
SandboxController -> SandboxPolicy -> DockerSandboxProvider
```

上层可以请求下层提供能力；下层不应反向 import Planner、GraphRunner 或 CrewAI。尤其是 `app/domain/<domain>/service.py` 不应依赖 Agent、Gateway 或 LLM。

## 领域能力表

| Domain / Agent | 持久化输出 | 可读取 | 可写入 / 可执行 |
|---|---|---|---|
| requirement | `requirement.md` | 无前置 artifact | 保存、加载需求 |
| architecture | `architecture.md` | requirement | 保存架构文档 |
| task | `tasks.md` | requirement、architecture | 保存任务清单 |
| bootstrap | `environment.md`、`runtime.yaml`、可选 `requirements.in` | requirement、architecture、tasks | 声明 profile 和依赖意图；不安装、不联网 |
| code | `implementation.md`、`workspace/` 源文件 | requirement、architecture、tasks、runtime 状态 | 读写允许的 workspace 文件；不运行命令 |
| test | `tests.md`、`workspace/tests/` | requirement、tasks、implementation | 写测试；请求固定 sandbox `unit` check |
| review | `review.md` | requirement、architecture、tasks、implementation、tests、runtime 状态、workspace | 只读审查并保存报告 |

`TaskAgent` 的当前职责是生成给人和后续 Agent 使用的 `tasks.md`。它会在下一个闭环阶段被 `WorkItem` 控制面逐步替代；Planner 才是任务拆分与状态推进的最终所有者。

## 工具路径

```text
Domain ToolSet registration
  -> ToolGateway.register_toolset(domain, name, toolset)
  -> ToolCatalog stores ToolDef + ToolSource registration
  -> ToolAccessPolicy filters visible sources
  -> ToolGateway.tools_for(domain)
  -> ProjectOSTool (CrewAI BaseTool)
  -> ToolSource.execute(...)
  -> Domain Service
```

本地 ToolSet 在启动时注册，默认 `always` 可见。未来 MCP 会使用同一目录，但属于动态 `ToolSource`：只有上层按 source 授权后才会 discover 和暴露。

## 项目文件模型

```text
projects/<project>/
  project.yaml                 项目元信息
  runtime.yaml                 不可信运行时声明，只能选择白名单 profile
  requirements.in              可选依赖意图，不是可执行安装脚本
  requirement.md
  architecture.md
  tasks.md
  environment.md
  implementation.md
  tests.md
  review.md
  workspace/                   被生成项目的源代码和测试
  .sandbox/wheels/<digest>/    仅 DependencyResolver 写入的依赖缓存
```

## 运行时与安全边界

`RuntimeCatalog` 是受信任白名单，当前只有 `python-stdlib` 与 `python-pip`。`runtime.yaml` 不能指定镜像、命令、挂载或网络。

测试容器固定使用无网络、只读根文件系统、非 root 用户、无 capabilities、PID/CPU/内存/时间/输出限制，并且只读挂载 `workspace/`。不挂载 Docker socket、用户目录、`.env`、ProjectOS 源码或任何宿主机密钥。

## 当前成熟度

| 能力 | 状态 |
|---|---|
| 七领域 Agent、ToolSet 与线性执行 | 已实现 |
| Planner 受控草案和计划校验 | 已实现（v0） |
| Python 标准库 Docker 测试 | 已实现并真实验证 |
| 测试失败后的局部重规划与重试 | 未实现 |
| 持久化执行证据 | 未实现 |
| `python-pip` 批准与恢复 | Resolver 已有，Workflow 未接入 |
| 真实 MCP connector | 未实现 |
