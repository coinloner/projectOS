# Runtime 模块

## 概述

`app.runtime.Runtime` 是 ProjectOS 的命令执行层。**所有模块的 shell 命令调用必须经由 `Runtime.run()`**，不得直接使用 `subprocess` 或其他方式。后续的权限校验、命令白名单、执行日志等功能均在此层统一实现。

## 依赖

| 模块 | 用途 |
|---|---|
| `subprocess` | 实际执行命令 |
| `typing.Optional` | 类型标注 |

## 类设计

```
Runtime
└── run(command, cwd?) -> dict   # 静态方法：执行命令
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
