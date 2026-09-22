"""测试 Schema 演化和版本管理功能。"""

import pytest
from datetime import datetime

from app.orchestration.schema_registry import (
    FieldSchema,
    FieldType,
    ModelSchema,
    SchemaRegistry,
)
from app.orchestration.schema_evolution import (
    ChangeType,
    CompatibilityLevel,
    SchemaChange,
    SchemaVersion,
    SchemaComparator,
    SchemaEvolutionManager,
)


def test_schema_change_creation():
    """测试创建 SchemaChange。"""
    change = SchemaChange(
        change_type=ChangeType.FIELD_ADDED,
        compatibility=CompatibilityLevel.BACKWARD_COMPATIBLE,
        model_name="Task",
        field_name="priority",
        new_value="integer",
        description="新增优先级字段",
    )

    assert change.change_type == ChangeType.FIELD_ADDED
    assert change.compatibility == CompatibilityLevel.BACKWARD_COMPATIBLE
    assert not change.is_breaking()


def test_schema_version_creation():
    """测试创建 SchemaVersion。"""
    registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
    ))

    version = SchemaVersion(
        version="1.0.0",
        registry=registry,
        timestamp=datetime.now().isoformat(),
        author="test",
        message="Initial version",
    )

    assert version.version == "1.0.0"
    assert len(version.registry.models) == 1


def test_compare_registries_field_added():
    """测试检测字段新增。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING),
            ),
            phase="architecture",
        ),
    ))

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING),
                FieldSchema(name="description", type=FieldType.STRING, optional=True),
            ),
            phase="architecture",
        ),
    ))

    changes = SchemaComparator.compare_registries(old_registry, new_registry)

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FIELD_ADDED
    assert changes[0].field_name == "description"
    assert changes[0].compatibility == CompatibilityLevel.BACKWARD_COMPATIBLE


def test_compare_registries_field_removed():
    """测试检测字段删除。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING),
            ),
            phase="architecture",
        ),
    ))

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
            ),
            phase="architecture",
        ),
    ))

    changes = SchemaComparator.compare_registries(old_registry, new_registry)

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FIELD_REMOVED
    assert changes[0].field_name == "title"
    assert changes[0].compatibility == CompatibilityLevel.BREAKING_CHANGE


def test_compare_registries_field_type_changed():
    """测试检测字段类型变更。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.INTEGER),
            ),
            phase="architecture",
        ),
    ))

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
            ),
            phase="architecture",
        ),
    ))

    changes = SchemaComparator.compare_registries(old_registry, new_registry)

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FIELD_TYPE_CHANGED
    assert changes[0].compatibility == CompatibilityLevel.BREAKING_CHANGE


def test_compare_registries_field_optional_changed():
    """测试检测字段可选性变更。"""
    # 从必填变为可选 (向后兼容)
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="title", type=FieldType.STRING, optional=False),
            ),
            phase="architecture",
        ),
    ))

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="title", type=FieldType.STRING, optional=True),
            ),
            phase="architecture",
        ),
    ))

    changes = SchemaComparator.compare_registries(old_registry, new_registry)

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FIELD_OPTIONAL_CHANGED
    assert changes[0].compatibility == CompatibilityLevel.BACKWARD_COMPATIBLE

    # 从可选变为必填 (破坏性)
    old_registry2 = new_registry
    new_registry2 = old_registry

    changes2 = SchemaComparator.compare_registries(old_registry2, new_registry2)

    assert len(changes2) == 1
    assert changes2[0].compatibility == CompatibilityLevel.BREAKING_CHANGE


def test_compare_registries_model_added():
    """测试检测模型新增。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
    ))

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
        ModelSchema(
            name="User",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
    ))

    changes = SchemaComparator.compare_registries(old_registry, new_registry)

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.MODEL_ADDED
    assert changes[0].model_name == "User"
    assert changes[0].compatibility == CompatibilityLevel.FORWARD_COMPATIBLE


def test_suggest_version_bump_breaking():
    """测试破坏性变更的版本号建议。"""
    changes = [
        SchemaChange(
            change_type=ChangeType.FIELD_REMOVED,
            compatibility=CompatibilityLevel.BREAKING_CHANGE,
            model_name="Task",
            field_name="title",
        )
    ]

    new_version = SchemaComparator.suggest_version_bump(changes, "1.2.3")
    assert new_version == "2.0.0"


def test_suggest_version_bump_feature():
    """测试新功能的版本号建议。"""
    changes = [
        SchemaChange(
            change_type=ChangeType.FIELD_ADDED,
            compatibility=CompatibilityLevel.BACKWARD_COMPATIBLE,
            model_name="Task",
            field_name="priority",
        )
    ]

    new_version = SchemaComparator.suggest_version_bump(changes, "1.2.3")
    assert new_version == "1.3.0"


def test_suggest_version_bump_patch():
    """测试修复的版本号建议。"""
    changes = [
        SchemaChange(
            change_type=ChangeType.FIELD_CONSTRAINT_CHANGED,
            compatibility=CompatibilityLevel.BACKWARD_COMPATIBLE,
            model_name="Task",
            field_name="title",
        )
    ]

    new_version = SchemaComparator.suggest_version_bump(changes, "1.2.3")
    assert new_version == "1.2.4"


def test_create_new_version():
    """测试创建新版本。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
            ),
            phase="architecture",
        ),
    ))

    old_version = SchemaVersion(
        version="1.0.0",
        registry=old_registry,
        timestamp=datetime.now().isoformat(),
    )

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING, optional=True),
            ),
            phase="architecture",
        ),
    ))

    new_version = SchemaEvolutionManager.create_new_version(
        old_version,
        new_registry,
        author="test",
        message="Add title field",
    )

    assert new_version.version == "1.1.0"  # minor bump for new field
    assert len(new_version.changes) == 1
    assert new_version.author == "test"


def test_validate_upgrade_path_safe():
    """测试安全的升级路径。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
    ))

    old_version = SchemaVersion(
        version="1.0.0",
        registry=old_registry,
        timestamp=datetime.now().isoformat(),
    )

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING, optional=True),
            ),
            phase="architecture",
        ),
    ))

    new_version = SchemaVersion(
        version="1.1.0",
        registry=new_registry,
        timestamp=datetime.now().isoformat(),
    )

    is_safe, warnings = SchemaEvolutionManager.validate_upgrade_path(old_version, new_version)

    assert is_safe
    assert len(warnings) == 0


def test_validate_upgrade_path_breaking():
    """测试包含破坏性变更的升级路径。"""
    old_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
                FieldSchema(name="title", type=FieldType.STRING),
            ),
            phase="architecture",
        ),
    ))

    old_version = SchemaVersion(
        version="1.0.0",
        registry=old_registry,
        timestamp=datetime.now().isoformat(),
    )

    new_registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(
                FieldSchema(name="id", type=FieldType.UUID),
            ),
            phase="architecture",
        ),
    ))

    new_version = SchemaVersion(
        version="2.0.0",
        registry=new_registry,
        timestamp=datetime.now().isoformat(),
    )

    is_safe, warnings = SchemaEvolutionManager.validate_upgrade_path(old_version, new_version)

    assert not is_safe
    assert len(warnings) > 0
    assert "破坏性变更" in warnings[0]


def test_schema_version_serialization():
    """测试 SchemaVersion 序列化和反序列化。"""
    registry = SchemaRegistry(models=(
        ModelSchema(
            name="Task",
            fields=(FieldSchema(name="id", type=FieldType.UUID),),
            phase="architecture",
        ),
    ))

    original = SchemaVersion(
        version="1.0.0",
        registry=registry,
        timestamp=datetime.now().isoformat(),
        changes=(
            SchemaChange(
                change_type=ChangeType.MODEL_ADDED,
                compatibility=CompatibilityLevel.FORWARD_COMPATIBLE,
                model_name="Task",
            ),
        ),
        author="test",
        message="Initial version",
    )

    # 转换为字典
    data = original.to_dict()
    assert "version" in data
    assert "registry" in data
    assert "changes" in data

    # 从字典恢复
    restored = SchemaVersion.from_dict(data)
    assert restored.version == original.version
    assert len(restored.changes) == len(original.changes)
    assert restored.author == original.author
