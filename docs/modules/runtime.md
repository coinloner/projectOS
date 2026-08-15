# Runtime 模块

## 概述

`app.runtime.Runtime` 是 ProjectOS 自身的受控命令执行层，仅用于未来人工确认的 Shell 逃生舱。生成项目的构建、运行和测试必须使用 `SandboxController`，不能使用此模块。

未来 Workflow 的 Shell 逃生舱必须使用 `Runtime.run_checked()`，这样命令白名单、cwd 校验、日志审计可以集中在 Runtime 一处实现。

## 依赖

| 模块 | 用途 |
|---|---|
| `subprocess` | 实际执行命令 |
| `pathlib.Path` | cwd 解析和校验 |
| `typing.Optional` | 类型标注 |

## 类设计

```
Runtime
├── run(command, cwd?) -> dict
└── run_checked(command, cwd, allowed_commands) -> dict
```

## 方法签名

### `run(command: list, cwd: Optional[str] = None) -> dict`

**参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `command` | `list` | 是 | 命令参数列表，例如 `["python", "--version"]` |
| `cwd` | `str \| None` | 否 | 工作目录，不传则沿用进程当前目录 |

**返回值**

| 字段 | 类型 | 说明 |
|---|---|---|
| `stdout` | `str` | 标准输出 |
| `stderr` | `str` | 标准错误 |
| `returncode` | `int` | 退出码，0 表示成功 |

**注意事项**

- `command` 必须为列表形式，避免 shell 注入风险。
- 调用方需自行检查 `returncode` 并决定如何处理非零退出。

### `run_checked(command: list, cwd: str, allowed_commands: list) -> dict`

经过基础权限校验后执行命令。

| 校验 | 说明 |
|---|---|
| `command` | 必须是非空 `list[str]` |
| 白名单 | `command[0]` 或其 basename 必须在 `allowed_commands` 中 |
| `cwd` | 必须存在且是目录 |

`run_checked()` 内部复用 `Runtime.run()`。它只负责基础命令安全，不负责用户确认；用户确认属于 Workflow。
