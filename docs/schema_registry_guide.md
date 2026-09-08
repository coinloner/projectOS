# Schema Registry 使用指南

## 概述

Schema Registry 是 ProjectOS 中用于解决多 agent 协作时数据模型规范不一致问题的核心机制。它提供了一个权威的数据模型定义，确保所有 agent（architecture、code、test、integration）遵循同一份契约。

## 问题背景

在多 agent 协作的 delivery workflow 中，常见的问题是：

- **架构 agent** 定义了 `completed` 布尔字段
- **代码 agent** 正确实现了 `completed` 字段
- **仓储 agent** 却使用了 `status` 枚举字段
- **测试 agent** 期望 `TaskStatus` 枚举和 `updated_at` 字段

这导致模型定义、数据库表结构、测试代码之间互不兼容，需要人工修复。

## 解决方案

Schema Registry 提供：

1. **权威的数据模型定义** (`schemas.json`)
2. **自动化的一致性验证**
3. **版本管理和演化机制**
4. **破坏性变更检测**

## 核心概念

### 1. FieldSchema

单个字段的定义：

```python
from app.orchestration.schema_registry import FieldSchema, FieldType

field = FieldSchema(
    name="title",
    type=FieldType.STRING,
    optional=False,
    description="任务标题",
    max_length=200,
    min_length=1,
)
```

支持的字段类型：
- `STRING`: 字符串
- `INTEGER`: 整数
- `FLOAT`: 浮点数
- `BOOLEAN`: 布尔值
- `DATETIME`: 日期时间
- `DATE`: 日期
- `UUID`: UUID
- `JSON`: JSON 对象

### 2. ModelSchema

单个数据模型的定义：

```python
from app.orchestration.schema_registry import ModelSchema

model = ModelSchema(
    name="Task",
    fields=(
        FieldSchema(name="id", type=FieldType.UUID),
        FieldSchema(name="title", type=FieldType.STRING, max_length=200),
        FieldSchema(name="completed", type=FieldType.BOOLEAN, default=False),
    ),
    phase="architecture",
    description="任务领域模型",
)
```

### 3. SchemaRegistry

整个项目的 schema 注册表：

```python
from app.orchestration.schema_registry import SchemaRegistry

registry = SchemaRegistry(
    models=(task_model, user_model),
    version="1.0.0",
)

# 保存到文件
registry.save_to_file("schemas.json")

# 从文件加载
registry = SchemaRegistry.load_from_file("schemas.json")
```

## 工作流程

### 1. Architecture Agent 生成 Schema

在架构设计阶段，Architecture Agent 会生成 `schemas.json`：

```json
{
  "version": "1.0",
  "models": [
    {
      "name": "Task",
      "fields": [
        {
          "name": "id",
          "type": "uuid",
          "optional": false,
          "description": "任务唯一标识"
        },
        {
          "name": "title",
          "type": "string",
          "optional": false,
          "max_length": 200,
          "min_length": 1
        },
        {
          "name": "completed",
          "type": "boolean",
          "optional": false,
          "default": false
        },
        {
          "name": "created_at",
          "type": "datetime",
          "optional": false
        }
      ],
      "phase": "architecture",
      "description": "任务领域模型"
    }
  ]
}
```

### 2. Code Agent 读取并遵循 Schema

Code Agent 在实现代码时：

```python
# 1. 读取 schema
schemas = load_artifact('schemas')

# 2. 严格按照 schema 实现模型
class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(..., min_length=1, max_length=200)
    completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### 3. Test Agent 读取并遵循 Schema

Test Agent 在编写测试时：

```python
# 1. 读取 schema
schemas = load_artifact('schemas')

# 2. 使用 schema 中定义的确切字段
def test_task_fields():
    task = Task(title="Test", completed=False)
    assert task.completed is False  # 使用 completed，不是 status
```

### 4. Integration Agent 验证一致性

Integration Agent 在代码集成时自动验证：

```python
from app.orchestration.schema_validation_tool import validate_schemas_in_workspace

result = validate_schemas_in_workspace(workspace_path)

if result["has_violations"]:
    # 发现错误，阻止合并
    raise ValidationError(result["summary"])
```

## Schema 验证

### 自动验证

Integration Agent 会自动验证：

1. **Pydantic 模型定义**：字段名、类型是否与 schema 匹配
2. **数据库表结构**：SQL CREATE TABLE 语句中的字段
3. **测试代码**：测试中引用的字段是否存在
4. **API 端点**：FastAPI 路由中的字段引用

### 手动验证

```python
from app.orchestration.schema_validator import SchemaValidator, validate_workspace_schemas

# 加载 schema
registry = SchemaRegistry.load_from_file("schemas.json")

# 验证整个 workspace
violations = validate_workspace_schemas(workspace_path, registry)

for violation in violations:
    print(f"{violation.severity}: {violation.message}")
    if violation.file_path:
        print(f"  文件: {violation.file_path}:{violation.line_number}")
```

### 验证结果

```
❌ Schema 验证发现 2 个错误, 1 个警告:

**错误 (阻塞合并):**
1. 模型字段 'status' 不在 schema 中 (backend/app/models.py)
2. 表字段 'updated_at' 不在 schema 中

**警告 (不阻塞合并):**
1. 可能引用了不存在的字段 'priority' (tests/test_models.py:42)
```

## Schema 演化

### 版本管理

使用 Semantic Versioning：

- **Major (2.0.0)**: 破坏性变更（删除字段、修改类型、必填字段）
- **Minor (1.1.0)**: 向后兼容的新功能（新增可选字段、新增模型）
- **Patch (1.0.1)**: 向后兼容的修复（放宽约束）

### 比较版本

```python
from app.orchestration.schema_evolution import SchemaComparator

# 比较两个 schema
changes = SchemaComparator.compare_registries(old_registry, new_registry)

for change in changes:
    print(f"{change.change_type}: {change.description}")
    print(f"  兼容性: {change.compatibility}")
    if change.is_breaking():
        print("  ⚠️  破坏性变更！")
```

### 自动建议版本号

```python
from app.orchestration.schema_evolution import SchemaEvolutionManager

# 创建新版本
new_version = SchemaEvolutionManager.create_new_version(
    old_version,
    new_registry,
    author="architecture-agent",
    message="Add priority field to Task model",
)

print(f"建议版本号: {new_version.version}")
print(f"变更数量: {len(new_version.changes)}")
```

### 验证升级路径

```python
from app.orchestration.schema_evolution import SchemaEvolutionManager

is_safe, warnings = SchemaEvolutionManager.validate_upgrade_path(
    from_version,
    to_version,
)

if not is_safe:
    print("⚠️  升级包含破坏性变更:")
    for warning in warnings:
        print(f"  - {warning}")
```

## 最佳实践

### 1. 在架构阶段定义 Schema

```python
# Architecture Agent 应该生成完整的 schemas.json
registry = SchemaRegistry(
    models=(task_model, user_model),
    version="1.0.0",
)
registry.save_to_file(".projectos/schemas.json")
```

### 2. 所有实现 Agent 读取 Schema

```python
# Code Agent, Test Agent 都应该先读取 schema
try:
    schemas = load_artifact('schemas')
    # 严格按照 schemas 实现
except ArtifactNotFound:
    # 如果没有 schema，使用默认实现
    pass
```

### 3. Integration 阶段强制验证

```python
# Integration Agent 应该在合并前验证
result = validate_schemas_in_workspace(workspace_path)
if result["has_violations"]:
    return IntegrationReview(
        verdict="needs_fix",
        rationale="Schema 验证失败，存在字段不匹配问题",
        findings=[
            IntegrationFinding(
                code="schema.violation",
                severity="blocker",
                summary=result["summary"],
            )
        ],
    )
```

### 4. 演化时遵循语义版本

```python
# 破坏性变更必须增加 major 版本
if SchemaComparator.has_breaking_changes(changes):
    new_version = f"{major + 1}.0.0"
else:
    new_version = f"{major}.{minor + 1}.0"
```

## 故障排查

### 问题：Schema 验证失败

**症状**：Integration Agent 报告 schema 违规

**解决方案**：
1. 检查 `schemas.json` 是否存在
2. 确认模型定义与 schema 一致
3. 查看具体的违规信息，定位问题文件和行号
4. 修复不一致的字段名或类型

### 问题：找不到 Schema

**症状**：Code Agent 或 Test Agent 无法加载 schema

**解决方案**：
1. 确认 Architecture Agent 生成了 `schemas.json`
2. 检查 `DeliveryContract` 中是否包含 `schemas.json` artifact
3. 确认 schema 文件在正确的位置

### 问题：Schema 变更后代码失效

**症状**：升级 schema 版本后，现有代码编译失败

**解决方案**：
1. 使用 `SchemaComparator` 检查变更
2. 确认是否为破坏性变更
3. 如果是破坏性变更，更新所有相关代码
4. 考虑使用数据库迁移脚本

## 示例项目

查看 `projects/taskflow` 项目的完整示例：

```bash
# 查看 schema 定义
cat projects/taskflow/.projectos/schemas.json

# 运行验证
pytest tests/test_schema_registry.py
pytest tests/test_schema_evolution.py

# 查看修复后的代码
cat projects/taskflow/workspace/backend/app/models.py
cat projects/taskflow/workspace/backend/app/repository.py
cat projects/taskflow/workspace/tests/test_models.py
```

## 总结

Schema Registry 通过提供权威的数据模型定义和自动化验证，解决了多 agent 协作中的规范不一致问题。遵循本指南，可以确保：

1. ✅ 所有 agent 使用一致的数据模型
2. ✅ 字段名、类型、约束完全匹配
3. ✅ 破坏性变更被及时发现
4. ✅ Schema 演化有据可查

---

更多信息请参考：
- `app/orchestration/schema_registry.py` - 核心实现
- `app/orchestration/schema_validator.py` - 验证工具
- `app/orchestration/schema_evolution.py` - 演化管理
- `tests/test_schema_*.py` - 测试用例
