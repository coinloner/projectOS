"""测试 Schema Registry 核心功能。"""

import pytest
from pathlib import Path
import tempfile
import json

from app.orchestration.schema_registry import (
    FieldSchema,
    FieldType,
    ModelSchema,
    SchemaRegistry,
    SchemaViolation,
    create_example_schema,
)


def test_field_schema_creation():
    """测试创建 FieldSchema。"""
    field = FieldSchema(
        name="title",
        type=FieldType.STRING,
        optional=False,
        description="任务标题",
        max_length=200,
    )

    assert field.name == "title"
    assert field.type == FieldType.STRING
    assert field.optional is False
    assert field.max_length == 200


def test_field_schema_validation():
    """测试 FieldSchema 验证。"""
    with pytest.raises(ValueError, match="name 不能为空"):
        FieldSchema(name="", type=FieldType.STRING)


def test_model_schema_creation():
    """测试创建 ModelSchema。"""
    fields = (
        FieldSchema(name="id", type=FieldType.UUID),
        FieldSchema(name="title", type=FieldType.STRING),
    )

    model = ModelSchema(
        name="Task",
        fields=fields,
        phase="architecture",
        description="任务模型",
    )

    assert model.name == "Task"
    assert len(model.fields) == 2
    assert model.phase == "architecture"


def test_model_schema_duplicate_fields():
    """测试 ModelSchema 检测重复字段。"""
    fields = (
        FieldSchema(name="id", type=FieldType.UUID),
        FieldSchema(name="id", type=FieldType.STRING),  # 重复
    )

    with pytest.raises(ValueError, match="重复的字段名"):
        ModelSchema(name="Task", fields=fields, phase="architecture")


def test_model_schema_get_field():
    """测试 ModelSchema.get_field。"""
    fields = (
        FieldSchema(name="id", type=FieldType.UUID),
        FieldSchema(name="title", type=FieldType.STRING),
    )

    model = ModelSchema(name="Task", fields=fields, phase="architecture")

    assert model.get_field("id") is not None
    assert model.get_field("title") is not None
    assert model.get_field("nonexistent") is None


def test_schema_registry_creation():
    """测试创建 SchemaRegistry。"""
    task_schema = ModelSchema(
        name="Task",
        fields=(FieldSchema(name="id", type=FieldType.UUID),),
        phase="architecture",
    )

    registry = SchemaRegistry(models=(task_schema,))

    assert len(registry.models) == 1
    assert registry.get_model("Task") is not None


def test_schema_registry_duplicate_models():
    """测试 SchemaRegistry 检测重复模型。"""
    task_schema1 = ModelSchema(
        name="Task",
        fields=(FieldSchema(name="id", type=FieldType.UUID),),
        phase="architecture",
    )
    task_schema2 = ModelSchema(
        name="Task",
        fields=(FieldSchema(name="title", type=FieldType.STRING),),
        phase="architecture",
    )

    with pytest.raises(ValueError, match="重复的模型名"):
        SchemaRegistry(models=(task_schema1, task_schema2))


def test_schema_registry_validate_field_reference():
    """测试 SchemaRegistry.validate_field_reference。"""
    task_schema = ModelSchema(
        name="Task",
        fields=(
            FieldSchema(name="id", type=FieldType.UUID),
            FieldSchema(name="title", type=FieldType.STRING),
        ),
        phase="architecture",
    )

    registry = SchemaRegistry(models=(task_schema,))

    assert registry.validate_field_reference("Task", "id") is True
    assert registry.validate_field_reference("Task", "title") is True
    assert registry.validate_field_reference("Task", "nonexistent") is False
    assert registry.validate_field_reference("NonexistentModel", "id") is False


def test_schema_registry_serialization():
    """测试 SchemaRegistry 序列化和反序列化。"""
    original = create_example_schema()

    # 转换为字典
    data = original.to_dict()
    assert "models" in data
    assert len(data["models"]) == 1

    # 从字典恢复
    restored = SchemaRegistry.from_dict(data)
    assert len(restored.models) == len(original.models)
    assert restored.models[0].name == original.models[0].name


def test_schema_registry_json_serialization():
    """测试 SchemaRegistry JSON 序列化。"""
    original = create_example_schema()

    # 转换为 JSON
    json_str = original.to_json()
    assert isinstance(json_str, str)

    # 解析 JSON
    data = json.loads(json_str)
    assert "models" in data

    # 从 JSON 恢复
    restored = SchemaRegistry.from_json(json_str)
    assert len(restored.models) == len(original.models)


def test_schema_registry_file_operations():
    """测试 SchemaRegistry 文件读写。"""
    original = create_example_schema()

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "schemas.json"

        # 保存到文件
        original.save_to_file(file_path)
        assert file_path.exists()

        # 从文件加载
        restored = SchemaRegistry.load_from_file(file_path)
        assert len(restored.models) == len(original.models)
        assert restored.models[0].name == original.models[0].name


def test_create_example_schema():
    """测试创建示例 schema。"""
    registry = create_example_schema()

    assert len(registry.models) == 1

    task_model = registry.get_model("Task")
    assert task_model is not None
    assert task_model.name == "Task"
    assert len(task_model.fields) == 5

    # 验证字段
    assert task_model.has_field("id")
    assert task_model.has_field("title")
    assert task_model.has_field("description")
    assert task_model.has_field("completed")
    assert task_model.has_field("created_at")

    # 验证字段类型
    id_field = task_model.get_field("id")
    assert id_field.type == FieldType.UUID

    title_field = task_model.get_field("title")
    assert title_field.type == FieldType.STRING
    assert title_field.max_length == 200

    completed_field = task_model.get_field("completed")
    assert completed_field.type == FieldType.BOOLEAN
    assert completed_field.default is False


def test_schema_violation_creation():
    """测试创建 SchemaViolation。"""
    violation = SchemaViolation(
        severity="error",
        message="字段不匹配",
        file_path="models.py",
        line_number=42,
        model_name="Task",
        field_name="status",
    )

    assert violation.severity == "error"
    assert violation.message == "字段不匹配"
    assert violation.file_path == "models.py"
    assert violation.line_number == 42


def test_schema_violation_invalid_severity():
    """测试 SchemaViolation 验证严重级别。"""
    with pytest.raises(ValueError, match="invalid severity"):
        SchemaViolation(severity="invalid", message="test")
