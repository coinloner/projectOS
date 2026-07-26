# Requirement API 接口文档

> **定位**：定义 `Requirement` 模块对外的公共契约。所有实现变更不得破坏此文档中声明的签名、返回值结构和异常语义。

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

## 2. 保存需求文档

```python
Requirement.save(project_path: str, document: RequirementDocument) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `project_path` | `str` | 是 | 项目根目录路径 |
| `document` | `RequirementDocument` | 是 | 待写入的需求文档对象 |

- 写入路径：`{project_path}/requirement.md`
- 父目录不存在时自动创建
- 编码：UTF-8
- 不抛异常（I/O 错误除外）

---

## 3. 读取需求文档

```python
Requirement.load(project_path: str) -> RequirementDocument
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `project_path` | `str` | 是 | 项目根目录路径 |

| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | `{project_path}/requirement.md` 不存在 |

- 返回值：包含文件全部文本内容的 `RequirementDocument`
- 编码：UTF-8

---

## 4. 更新需求文档

```python
Requirement.update(project_path: str, document: RequirementDocument) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `project_path` | `str` | 是 | 项目根目录路径 |
| `document` | `RequirementDocument` | 是 | 更新后的需求文档对象 |

- 当前实现等价于 `save()`，为全量覆盖写入
- 保留此方法作为未来扩展点（差异对比、版本记录等）

---

## 兼容性约定

1. **数据结构稳定** —— `RequirementDocument` 的字段可扩展（增加可选字段），不得删除或修改 `content` 的类型。
2. **异常类型不得降级** —— `load()` 在文件不存在时必须抛出 `FileNotFoundError`，不得静默返回空文档。
3. **编码固定** —— 所有文件读写均使用 UTF-8 编码。
4. **新增方法自由** —— 在保持已有方法不变的前提下，可以增加新的静态/类方法。
5. **与 Project 模块的关系** —— `Requirement` 操作的文件位于 `Project.project_path` 下，但 `Requirement` 不依赖 `Project`，仅依赖路径字符串。
