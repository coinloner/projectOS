# Requirement API 接口文档

> **定位**：定义 `app.domain.requirement` 的公共契约。所有实现变更不得破坏此文档中声明的签名、返回值结构和异常语义。

## 1. RequirementDocument 数据结构

```python
@dataclass
class RequirementDocument:
    content: str
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 需求文档的完整 Markdown 内容，可以为空字符串 |

### 工厂方法

```python
RequirementDocument.empty() -> RequirementDocument
```

- 返回 `content=""` 的 `RequirementDocument` 实例
- 等价于 `RequirementDocument(content="")`，提供语义化入口

---

## 2. 创建需求服务与保存文档

```python
RequirementService(project_path: str)
RequirementService.save(document: RequirementDocument) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `document` | `RequirementDocument` | 是 | 待写入的需求文档对象 |

- 写入路径：`{project_path}/requirement.md`
- 父目录不存在时自动创建
- 编码：UTF-8
- 不抛异常（I/O 错误除外）

---

## 3. 读取需求文档

```python
RequirementService.load() -> RequirementDocument
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | `{project_path}/requirement.md` 不存在 |

- 返回值：包含文件全部文本内容的 `RequirementDocument`
- 编码：UTF-8

---

## 4. 更新需求文档

```python
RequirementService.update(document: RequirementDocument) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `document` | `RequirementDocument` | 是 | 更新后的需求文档对象 |

- 当前实现等价于 `save()`，为全量覆盖写入
- 保留此方法作为未来扩展点（差异对比、版本记录等）

---

## 5. RequirementToolSet（Agent 本地适配）

`RequirementToolSet` 将 `RequirementService` 封装为 Agent 可调用的本地操作；同一 `tools.py` 中的注册函数再将其注册为 ToolGateway 工具。

### 构造函数

```python
RequirementToolSet(service: RequirementService) -> RequirementToolSet
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `service` | `RequirementService` | 是 | 已绑定项目路径的需求领域服务 |

### save

```python
RequirementToolSet.save_requirement(content: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | `str` | 是 | 需求文档的 Markdown 内容 |

- 返回 `"需求文档已保存"`
- 不抛异常

### load

```python
RequirementToolSet.load_requirement() -> str
```

- 文件存在 → 返回文档内容
- 文件不存在 → 返回 `"（尚未创建需求文档）"`
- 不抛异常

---

## 兼容性约定

1. **数据结构稳定** —— `RequirementDocument` 的字段可扩展（增加可选字段），不得删除或修改 `content` 的类型。
2. **异常类型不得降级** —— `load()` 在文件不存在时必须抛出 `FileNotFoundError`，不得静默返回空文档。
3. **编码固定** —— 所有文件读写均使用 UTF-8 编码。
4. **新增方法自由** —— 在保持已有方法不变的前提下，可以增加新的实例/类方法。
5. **与 Project 模块的关系** —— `RequirementService` 操作的文件位于 `Project.project_path` 下，但不依赖 `Project`，仅在构造时接收路径字符串。
