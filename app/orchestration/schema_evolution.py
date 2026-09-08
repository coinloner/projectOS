"""Schema 演化和版本管理机制。

该模块实现:
1. Schema 版本化 (semantic versioning)
2. Schema 迁移和兼容性检查
3. 向后兼容性验证
4. Schema 变更历史追踪
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.orchestration.schema_registry import (
    FieldSchema,
    ModelSchema,
    SchemaRegistry,
)


class ChangeType(str, Enum):
    """Schema 变更类型。"""
    FIELD_ADDED = "field_added"
    FIELD_REMOVED = "field_removed"
    FIELD_TYPE_CHANGED = "field_type_changed"
    FIELD_OPTIONAL_CHANGED = "field_optional_changed"
    FIELD_CONSTRAINT_CHANGED = "field_constraint_changed"
    MODEL_ADDED = "model_added"
    MODEL_REMOVED = "model_removed"
    MODEL_RENAMED = "model_renamed"


class CompatibilityLevel(str, Enum):
    """兼容性级别。"""
    BACKWARD_COMPATIBLE = "backward_compatible"  # 向后兼容
    FORWARD_COMPATIBLE = "forward_compatible"    # 向前兼容
    BREAKING_CHANGE = "breaking_change"          # 破坏性变更


@dataclass(frozen=True)
class SchemaChange:
    """Schema 变更记录。"""
    change_type: ChangeType
    compatibility: CompatibilityLevel
    model_name: str
    field_name: str | None = None
    old_value: Any = None
    new_value: Any = None
    description: str = ""

    def is_breaking(self) -> bool:
        """判断是否为破坏性变更。"""
        return self.compatibility == CompatibilityLevel.BREAKING_CHANGE

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "change_type": self.change_type.value,
            "compatibility": self.compatibility.value,
            "model_name": self.model_name,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
        }


@dataclass(frozen=True)
class SchemaVersion:
    """Schema 版本信息。"""
    version: str  # semantic version: "1.0.0"
    registry: SchemaRegistry
    timestamp: str  # ISO format
    changes: tuple[SchemaChange, ...] = ()
    author: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "version": self.version,
            "registry": self.registry.to_dict(),
            "timestamp": self.timestamp,
            "changes": [c.to_dict() for c in self.changes],
            "author": self.author,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaVersion:
        """从字典格式反序列化。"""
        changes = []
        for change_data in data.get("changes", []):
            changes.append(SchemaChange(
                change_type=ChangeType(change_data["change_type"]),
                compatibility=CompatibilityLevel(change_data["compatibility"]),
                model_name=change_data["model_name"],
                field_name=change_data.get("field_name"),
                old_value=change_data.get("old_value"),
                new_value=change_data.get("new_value"),
                description=change_data.get("description", ""),
            ))

        return cls(
            version=data["version"],
            registry=SchemaRegistry.from_dict(data["registry"]),
            timestamp=data["timestamp"],
            changes=tuple(changes),
            author=data.get("author", ""),
            message=data.get("message", ""),
        )


class SchemaComparator:
    """Schema 比较器，用于检测变更和兼容性。"""

    @staticmethod
    def compare_registries(
        old_registry: SchemaRegistry,
        new_registry: SchemaRegistry,
    ) -> list[SchemaChange]:
        """比较两个 Schema Registry，返回变更列表。"""
        changes = []

        old_models = {m.name: m for m in old_registry.models}
        new_models = {m.name: m for m in new_registry.models}

        # 检测新增的模型
        for model_name in new_models.keys() - old_models.keys():
            changes.append(SchemaChange(
                change_type=ChangeType.MODEL_ADDED,
                compatibility=CompatibilityLevel.FORWARD_COMPATIBLE,
                model_name=model_name,
                description=f"新增模型 {model_name}",
            ))

        # 检测删除的模型
        for model_name in old_models.keys() - new_models.keys():
            changes.append(SchemaChange(
                change_type=ChangeType.MODEL_REMOVED,
                compatibility=CompatibilityLevel.BREAKING_CHANGE,
                model_name=model_name,
                description=f"删除模型 {model_name}",
            ))

        # 检测修改的模型
        for model_name in old_models.keys() & new_models.keys():
            model_changes = SchemaComparator._compare_models(
                old_models[model_name],
                new_models[model_name],
            )
            changes.extend(model_changes)

        return changes

    @staticmethod
    def _compare_models(old_model: ModelSchema, new_model: ModelSchema) -> list[SchemaChange]:
        """比较两个模型，返回变更列表。"""
        changes = []

        old_fields = {f.name: f for f in old_model.fields}
        new_fields = {f.name: f for f in new_model.fields}

        # 检测新增的字段
        for field_name in new_fields.keys() - old_fields.keys():
            new_field = new_fields[field_name]
            # 新增可选字段是向后兼容的，新增必填字段是破坏性变更
            compatibility = (
                CompatibilityLevel.BACKWARD_COMPATIBLE if new_field.optional
                else CompatibilityLevel.BREAKING_CHANGE
            )
            changes.append(SchemaChange(
                change_type=ChangeType.FIELD_ADDED,
                compatibility=compatibility,
                model_name=old_model.name,
                field_name=field_name,
                new_value=new_field.type.value,
                description=f"新增字段 {field_name} ({new_field.type.value})",
            ))

        # 检测删除的字段
        for field_name in old_fields.keys() - new_fields.keys():
            old_field = old_fields[field_name]
            changes.append(SchemaChange(
                change_type=ChangeType.FIELD_REMOVED,
                compatibility=CompatibilityLevel.BREAKING_CHANGE,
                model_name=old_model.name,
                field_name=field_name,
                old_value=old_field.type.value,
                description=f"删除字段 {field_name}",
            ))

        # 检测修改的字段
        for field_name in old_fields.keys() & new_fields.keys():
            field_changes = SchemaComparator._compare_fields(
                old_model.name,
                old_fields[field_name],
                new_fields[field_name],
            )
            changes.extend(field_changes)

        return changes

    @staticmethod
    def _compare_fields(
        model_name: str,
        old_field: FieldSchema,
        new_field: FieldSchema,
    ) -> list[SchemaChange]:
        """比较两个字段，返回变更列表。"""
        changes = []

        # 检测类型变更
        if old_field.type != new_field.type:
            changes.append(SchemaChange(
                change_type=ChangeType.FIELD_TYPE_CHANGED,
                compatibility=CompatibilityLevel.BREAKING_CHANGE,
                model_name=model_name,
                field_name=old_field.name,
                old_value=old_field.type.value,
                new_value=new_field.type.value,
                description=f"字段 {old_field.name} 类型从 {old_field.type.value} 变更为 {new_field.type.value}",
            ))

        # 检测可选性变更
        if old_field.optional != new_field.optional:
            # 从必填变为可选是向后兼容的，从可选变为必填是破坏性变更
            compatibility = (
                CompatibilityLevel.BACKWARD_COMPATIBLE if new_field.optional
                else CompatibilityLevel.BREAKING_CHANGE
            )
            changes.append(SchemaChange(
                change_type=ChangeType.FIELD_OPTIONAL_CHANGED,
                compatibility=compatibility,
                model_name=model_name,
                field_name=old_field.name,
                old_value=old_field.optional,
                new_value=new_field.optional,
                description=f"字段 {old_field.name} {'变为可选' if new_field.optional else '变为必填'}",
            ))

        # 检测约束变更
        if (old_field.max_length != new_field.max_length or
            old_field.min_length != new_field.min_length):
            # 放宽约束是向后兼容的，收紧约束是破坏性变更
            compatibility = CompatibilityLevel.BACKWARD_COMPATIBLE
            if new_field.max_length and old_field.max_length:
                if new_field.max_length < old_field.max_length:
                    compatibility = CompatibilityLevel.BREAKING_CHANGE
            if new_field.min_length and old_field.min_length:
                if new_field.min_length > old_field.min_length:
                    compatibility = CompatibilityLevel.BREAKING_CHANGE

            changes.append(SchemaChange(
                change_type=ChangeType.FIELD_CONSTRAINT_CHANGED,
                compatibility=compatibility,
                model_name=model_name,
                field_name=old_field.name,
                old_value=f"min={old_field.min_length}, max={old_field.max_length}",
                new_value=f"min={new_field.min_length}, max={new_field.max_length}",
                description=f"字段 {old_field.name} 约束变更",
            ))

        return changes

    @staticmethod
    def has_breaking_changes(changes: list[SchemaChange]) -> bool:
        """判断变更列表中是否包含破坏性变更。"""
        return any(c.is_breaking() for c in changes)

    @staticmethod
    def suggest_version_bump(changes: list[SchemaChange], current_version: str) -> str:
        """根据变更建议新版本号。

        遵循 Semantic Versioning:
        - 破坏性变更: major bump (1.0.0 -> 2.0.0)
        - 向后兼容的新功能: minor bump (1.0.0 -> 1.1.0)
        - 向后兼容的修复: patch bump (1.0.0 -> 1.0.1)
        """
        parts = current_version.split(".")
        if len(parts) != 3:
            return "1.0.0"

        major, minor, patch = map(int, parts)

        if SchemaComparator.has_breaking_changes(changes):
            return f"{major + 1}.0.0"
        elif any(c.change_type in (ChangeType.FIELD_ADDED, ChangeType.MODEL_ADDED) for c in changes):
            return f"{major}.{minor + 1}.0"
        else:
            return f"{major}.{minor}.{patch + 1}"


class SchemaEvolutionManager:
    """Schema 演化管理器。"""

    @staticmethod
    def create_new_version(
        old_version: SchemaVersion,
        new_registry: SchemaRegistry,
        author: str = "",
        message: str = "",
    ) -> SchemaVersion:
        """创建新的 schema 版本。"""
        # 比较变更
        changes = SchemaComparator.compare_registries(
            old_version.registry,
            new_registry,
        )

        # 建议新版本号
        new_version_number = SchemaComparator.suggest_version_bump(
            changes,
            old_version.version,
        )

        return SchemaVersion(
            version=new_version_number,
            registry=new_registry,
            timestamp=datetime.now().isoformat(),
            changes=tuple(changes),
            author=author,
            message=message,
        )

    @staticmethod
    def validate_upgrade_path(
        from_version: SchemaVersion,
        to_version: SchemaVersion,
    ) -> tuple[bool, list[str]]:
        """验证升级路径是否安全。

        返回 (is_safe, warnings)
        """
        warnings = []

        changes = SchemaComparator.compare_registries(
            from_version.registry,
            to_version.registry,
        )

        breaking_changes = [c for c in changes if c.is_breaking()]
        if breaking_changes:
            warnings.append(f"包含 {len(breaking_changes)} 个破坏性变更:")
            for change in breaking_changes[:5]:  # 最多显示 5 个
                warnings.append(f"  - {change.description}")

        # 检查版本号是否符合语义版本规范
        from_parts = from_version.version.split(".")
        to_parts = to_version.version.split(".")

        if len(from_parts) == 3 and len(to_parts) == 3:
            from_major = int(from_parts[0])
            to_major = int(to_parts[0])

            if breaking_changes and to_major <= from_major:
                warnings.append(
                    f"破坏性变更应该增加 major 版本号，但 {from_version.version} -> {to_version.version}"
                )

        is_safe = len(breaking_changes) == 0
        return is_safe, warnings
