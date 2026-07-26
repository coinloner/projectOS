# Runtime API 接口文档

> **定位**：定义 `Runtime` 模块对外的公共契约。`Runtime` 是 ProjectOS 唯一的命令执行入口，所有模块的 shell 调用必须经由 `Runtime.run()`。

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

## 兼容性约定

1. **返回值结构不可变** —— `dict` 必须包含 `stdout`、`stderr`、`returncode` 三个键，类型不可改变。
2. **命令参数格式** —— `command` 必须为 `list` 类型，不支持字符串形式的 shell 命令，以避免 shell 注入风险。
3. **不自动抛异常** —— `Runtime.run()` 不会因命令失败而抛出异常。调用方需自行检查 `returncode` 并决定处理策略。
4. **后续扩展方向** —— 可在 `Runtime.run()` 内部增加：
   - 命令白名单校验
   - 执行日志记录
   - 超时控制（`timeout` 参数）
   - 环境变量注入
5. **唯一入口原则** —— 所有模块不得绕过 `Runtime` 直接使用 `subprocess`、`os.system`、`os.popen` 等。
