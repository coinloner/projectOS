# Runtime API 接口文档

> **定位**：定义 ProjectOS 自身人工确认 Shell 逃生舱的公共契约。生成项目的构建、运行和测试不使用该模块，而使用 SandboxController。

## 1. 执行命令

```python
Runtime.run(command: list, cwd: Optional[str] = None) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `command` | `list` | 是 | 命令参数列表，例如 `["git", "status"]` |
| `cwd` | `str \| None` | 否 | 工作目录，不传则沿用当前进程的 cwd |

### 返回值

```python
{
    "stdout": str,       # 标准输出
    "stderr": str,       # 标准错误
    "returncode": int,   # 退出码，0 表示成功
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `stdout` | `str` | 命令的标准输出（含尾部换行） |
| `stderr` | `str` | 命令的标准错误（含尾部换行） |
| `returncode` | `int` | 进程退出码，约定 0 = 成功 |

### 调用示例

```python
from app.runtime.Runtime import Runtime

# 获取 Python 版本
result = Runtime.run(["python", "--version"])
print(result["stdout"])        # "Python 3.12.x\n"
print(result["returncode"])    # 0

# 在指定目录下执行
result = Runtime.run(
    ["git", "init"],
    cwd="/path/to/project",
)
if result["returncode"] != 0:
    print(f"失败: {result['stderr']}")
```

---

## 2. 安全执行命令

```python
Runtime.run_checked(
    command: list,
    cwd: str,
    allowed_commands: list,
) -> dict
```

`run_checked()` 用于未来 Workflow 的 Shell 逃生舱。Workflow 负责人工确认，Runtime 负责命令校验和执行。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `command` | `list` | 是 | 命令参数列表 |
| `cwd` | `str` | 是 | 执行目录 |
| `allowed_commands` | `list` | 是 | 命令白名单 |

### 校验

| 校验 | 失败异常 |
|---|---|
| `command` 必须是非空 list | `RuntimeError` |
| command 每项必须是 str | `RuntimeError` |
| 命令不在白名单 | `PermissionError` |
| cwd 不存在或不是目录 | `RuntimeError` |

通过校验后复用 `Runtime.run(command, cwd)`。

---

## 兼容性约定

1. **返回值结构不可变** —— `dict` 必须包含 `stdout`、`stderr`、`returncode` 三个键，类型不可改变。
2. **命令参数格式** —— `command` 必须为 `list` 类型，不支持字符串形式的 shell 命令，以避免 shell 注入风险。
3. **不自动抛异常** —— `Runtime.run()` 不会因命令失败而抛出异常。调用方需自行检查 `returncode` 并决定处理策略。
4. **安全入口** —— 需要权限校验的命令必须使用 `Runtime.run_checked()`。
5. **生成项目隔离原则** —— 生成项目不得调用 `Runtime`；其构建、运行与测试必须通过 SandboxController。
