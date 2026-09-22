"""数据模型 Schema 注册表，用于在多 agent 协作中保证规范一致性。

该模块解决一个关键的编排问题：不同的实现 agent（code、test、repository）
可能基于各自的假设或默认模板生成代码，导致模型定义、数据库表结构、测试代码
之间出现字段不匹配。Schema Registry 提供一个权威的数据模型定义，所有 agent
必须读取并严格遵循。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class FieldType(str, Enum):
    """支持的字段类型枚举。"""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    DATE = "date"
    UUID = "uuid"
    JSON = "json"


@dataclass(frozen=True)
class FieldSchema:
    """单个字段的 schema 定义。"""
    name: str
    type: FieldType
    optional: bool = False
    description: str = ""
    max_length: int | None = None
    min_length: int | None = None
    default: Any = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("FieldSchema.name 不能为空")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式用于序列化。"""
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.type.value,
            "optional": self.optional,
        }
        if self.description:
            result["description"] = self.description
        if self.max_length is not None:
            result["max_length"] = self.max_length
        if self.min_length is not None:
            result["min_length"] = self.min_length
        if self.default is not None:
            result["default"] = self.default
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldSchema:
        """从字典格式反序列化。"""
        return cls(
            name=data["name"],
            type=FieldType(data["type"]),
            optional=data.get("optional", False),
            description=data.get("description", ""),
            max_length=data.get("max_length"),
            min_length=data.get("min_length"),
            default=data.get("default"),
        )


@dataclass(frozen=True)
class ModelSchema:
    """单个数据模型的 schema 定义。"""
    name: str
    fields: tuple[FieldSchema, ...]
    phase: str  # 在哪个阶段定义的（例如 "architecture", "implementation"）
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ModelSchema.name 不能为空")
        if not self.phase.strip():
            raise ValueError("ModelSchema.phase 不能为空")

        # 检查字段名唯一性
        field_names = [f.name for f in self.fields]
        if len(field_names) != len(set(field_names)):
            duplicates = [name for name in field_names if field_names.count(name) > 1]
            raise ValueError(f"ModelSchema {self.name} 包含重复的字段名: {duplicates}")

    def get_field(self, field_name: str) -> FieldSchema | None:
        """获取指定名称的字段 schema。"""
        return next((f for f in self.fields if f.name == field_name), None)

    def has_field(self, field_name: str) -> bool:
        """检查是否存在指定名称的字段。"""
        return self.get_field(field_name) is not None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式用于序列化。"""
        result: dict[str, Any] = {
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "phase": self.phase,
        }
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelSchema:
        """从字典格式反序列化。"""
        return cls(
            name=data["name"],
            fields=tuple(FieldSchema.from_dict(f) for f in data["fields"]),
            phase=data["phase"],
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class SchemaViolation:
    """Schema 违规记录。"""
    severity: str  # "error" | "warning"
    message: str
    file_path: str | None = None
    line_number: int | None = None
    model_name: str | None = None
    field_name: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in ("error", "warning"):
            raise ValueError(f"invalid severity: {self.severity}")


@dataclass(frozen=True)
class SchemaRegistry:
    """数据模型 Schema 注册表。"""
    models: tuple[ModelSchema, ...]
    version: str = "1.0"

    def __post_init__(self) -> None:
        # 检查模型名唯一性
        model_names = [m.name for m in self.models]
        if len(model_names) != len(set(model_names)):
            duplicates = [name for name in model_names if model_names.count(name) > 1]
            raise ValueError(f"SchemaRegistry 包含重复的模型名: {duplicates}")

    def get_model(self, name: str) -> ModelSchema | None:
        """获取指定名称的模型 schema。"""
        return next((m for m in self.models if m.name == name), None)

    def has_model(self, name: str) -> bool:
        """检查是否存在指定名称的模型。"""
        return self.get_model(name) is not None

    def validate_field_reference(self, model_name: str, field_name: str) -> bool:
        """验证某个字段引用是否合法。"""
        model = self.get_model(model_name)
        if not model:
            return False
        return model.has_field(field_name)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式用于序列化。"""
        return {
            "version": self.version,
            "models": [m.to_dict() for m in self.models],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaRegistry:
        """从字典格式反序列化。"""
        return cls(
            models=tuple(ModelSchema.from_dict(m) for m in data["models"]),
            version=data.get("version", "1.0"),
        )

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> SchemaRegistry:
        """从 JSON 字符串反序列化。"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def save_to_file(self, file_path: Path | str) -> None:
        """保存到 JSON 文件。"""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load_from_file(cls, file_path: Path | str) -> SchemaRegistry:
        """从 JSON 文件加载。"""
        path = Path(file_path)
        json_str = path.read_text(encoding="utf-8")
        return cls.from_json(json_str)


def create_example_schema() -> SchemaRegistry:
    """创建一个示例 schema registry，用于测试和文档。"""
    task_schema = ModelSchema(
        name="Task",
        fields=(
            FieldSchema(
                name="id",
                type=FieldType.UUID,
                optional=False,
                description="任务唯一标识",
            ),
            FieldSchema(
                name="title",
                type=FieldType.STRING,
                optional=False,
                description="任务标题",
                min_length=1,
                max_length=200,
            ),
            FieldSchema(
                name="description",
                type=FieldType.STRING,
                optional=True,
                description="任务描述",
                max_length=2000,
            ),
            FieldSchema(
                name="completed",
                type=FieldType.BOOLEAN,
                optional=False,
                description="是否完成",
                default=False,
            ),
            FieldSchema(
                name="created_at",
                type=FieldType.DATETIME,
                optional=False,
                description="创建时间",
            ),
        ),
        phase="architecture",
        description="任务领域模型",
    )

    return SchemaRegistry(models=(task_schema,))
