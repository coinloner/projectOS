# Requirement 模块

## 概述

`app.requirement.requirement.Requirement` 是 ProjectOS 的需求文档管理层，负责对项目目录下的 `requirement.md` 进行创建、读取和更新。所有文件操作均通过 `pathlib.Path` 完成。

## 依赖

| 模块 | 用途 |
|---|---|
| `dataclasses.dataclass` | 定义 `RequirementDocument` 数据类 |
| `pathlib.Path` | 跨平台路径操作 |

## 数据结构

### `RequirementDocument`

```python
@dataclass
class RequirementDocument:
    content: str
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 需求文档的完整 Markdown 内容 |

**类方法**

| 方法 | 返回值 | 说明 |
|---|---|---|
| `empty()` | `RequirementDocument` | 工厂方法，返回 `content=""` 的空文档 |

## 类设计

```
Requirement
├── _file_path(project_path) -> Path        # 私有静态：推导 requirement.md 路径
├── save(project_path, document) -> None     # 静态方法：写入文档
├── load(project_path) -> RequirementDocument # 静态方法：读取文档
└── update(project_path, document) -> None   # 静态方法：覆盖写入（复用 save）
```

## 方法签名

### `save(project_path: str, document: RequirementDocument) -> None`

将 `RequirementDocument` 的内容写入 `{project_path}/requirement.md`。

- 父目录不存在时自动创建（`mkdir(parents=True, exist_ok=True)`）
- 使用 UTF-8 编码写入

### `load(project_path: str) -> RequirementDocument`

读取 `{project_path}/requirement.md`，将内容封装为 `RequirementDocument` 返回。

| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | `requirement.md` 不存在 |

### `update(project_path: str, document: RequirementDocument) -> None`

覆盖写入 `requirement.md`，等价于 `save()`。保留此方法以提供语义化的更新入口，后续可在内部增加差异对比、版本记录等逻辑。

---

## RequirementToolSet

`app.requirement.requirement_tool.RequirementToolSet` 将 `Requirement` 的底层能力封装为 Agent 可调用的工具集。Agent 通过 ToolSet 访问 Tool，不直接依赖 `Requirement` 内部实现。

### 类设计

```
RequirementToolSet
├── __init__(project_path)    # 绑定项目路径
├── save(content) -> str      # 保存需求文档
└── load() -> str             # 读取需求文档
```

### 方法签名

#### `__init__(project_path: str)`

绑定目标项目路径，后续 `save()` / `load()` 在该项目下操作。

#### `save(content: str) -> str`

保存需求文档到 `{project_path}/requirement.md`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | `str` | 是 | 需求文档的 Markdown 内容 |

- 返回值：`"✅ 需求文档已保存"`
- 不抛异常

#### `load() -> str`

读取已有的需求文档。

- 文件存在 → 返回文档内容
- 文件不存在 → 返回 `"（尚未创建需求文档）"`
- 不抛异常

### 与 Agent 的协作

```python
# 启动时注册
tools = RequirementToolSet("./projects/MyProject")
registry.register("save_requirement", tools.save, ...)
registry.register("load_requirement", tools.load, ...)

# Agent 通过 Registry 调用，不直接 import Requirement
```

---

## 设计原则

- **纯数据模型**：`RequirementDocument` 是 `@dataclass`，不含业务逻辑，仅承载数据。
- **静态方法**：`Requirement` 的所有方法均为静态方法，无需实例化即可使用。
- **明确的异常语义**：`load()` 在文件不存在时抛出 `FileNotFoundError`，调用方无需猜测返回值。
- **保存即覆盖**：`save()` 和 `update()` 均为全量覆盖写入，当前不做差异合并。
- **ToolSet 不抛异常**：`RequirementToolSet` 的返回值均为字符串，错误以文本形式返回，不抛异常，便于 LLM 自我纠错。
