"""Schema 一致性验证工具，用于检查代码实现是否符合 Schema Registry。

该模块提供静态分析工具，检查：
1. Python 模型定义的字段是否与 schema 匹配
2. 测试代码引用的字段是否存在于 schema
3. 数据库表结构是否与 schema 一致
"""

from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.orchestration.schema_registry import (
    FieldSchema,
    FieldType,
    ModelSchema,
    SchemaRegistry,
    SchemaViolation,
)


class PydanticModelParser:
    """解析 Pydantic 模型定义的字段。"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.content = file_path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.content)

    def parse_model_fields(self, model_name: str) -> list[dict[str, Any]]:
        """解析指定模型的字段定义。"""
        fields = []

        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == model_name:
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        field_name = item.target.id
                        field_type = self._extract_type(item.annotation)
                        optional = self._is_optional(item.annotation)

                        fields.append({
                            "name": field_name,
                            "type": field_type,
                            "optional": optional,
                        })

        return fields

    def _extract_type(self, annotation: ast.expr) -> str:
        """提取字段类型。"""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                return annotation.value.id
        return "unknown"

    def _is_optional(self, annotation: ast.expr) -> bool:
        """判断字段是否可选。"""
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                return annotation.value.id == "Optional"
        return False


class SQLiteSchemaParser:
    """解析 SQLite 数据库表结构。"""

    @staticmethod
    def parse_create_table_sql(sql: str) -> list[dict[str, Any]]:
        """解析 CREATE TABLE SQL 语句，提取字段定义。"""
        fields = []

        # 提取字段定义部分
        match = re.search(r'CREATE TABLE.*?\((.*?)\)', sql, re.DOTALL | re.IGNORECASE)
        if not match:
            return fields

        fields_text = match.group(1)

        # 解析每个字段
        for line in fields_text.split(','):
            line = line.strip()
            if not line or line.upper().startswith('PRIMARY KEY') or line.upper().startswith('FOREIGN KEY'):
                continue

            parts = line.split()
            if len(parts) >= 2:
                field_name = parts[0].strip()
                field_type = parts[1].strip().upper()
                not_null = 'NOT NULL' in line.upper()

                fields.append({
                    "name": field_name,
                    "type": field_type,
                    "optional": not not_null,
                })

        return fields


class SchemaValidator:
    """Schema 一致性验证器。"""

    def __init__(self, registry: SchemaRegistry):
        self.registry = registry

    def validate_pydantic_model(
        self,
        file_path: Path,
        model_name: str,
        schema_model_name: str | None = None,
    ) -> list[SchemaViolation]:
        """验证 Pydantic 模型定义是否符合 schema。"""
        violations = []

        if schema_model_name is None:
            schema_model_name = model_name

        schema = self.registry.get_model(schema_model_name)
        if not schema:
            violations.append(SchemaViolation(
                severity="error",
                message=f"Schema 中未找到模型: {schema_model_name}",
                file_path=str(file_path),
            ))
            return violations

        try:
            parser = PydanticModelParser(file_path)
            actual_fields = parser.parse_model_fields(model_name)
        except Exception as e:
            violations.append(SchemaViolation(
                severity="error",
                message=f"解析 Pydantic 模型失败: {e}",
                file_path=str(file_path),
            ))
            return violations

        # 检查实际字段是否都在 schema 中
        actual_field_names = {f["name"] for f in actual_fields}
        schema_field_names = {f.name for f in schema.fields}

        for field_name in actual_field_names:
            if field_name not in schema_field_names:
                violations.append(SchemaViolation(
                    severity="error",
                    message=f"模型字段 '{field_name}' 不在 schema 中",
                    file_path=str(file_path),
                    model_name=model_name,
                    field_name=field_name,
                ))

        # 检查 schema 中的必填字段是否都存在
        for schema_field in schema.fields:
            if not schema_field.optional and schema_field.name not in actual_field_names:
                violations.append(SchemaViolation(
                    severity="error",
                    message=f"缺少 schema 中的必填字段 '{schema_field.name}'",
                    file_path=str(file_path),
                    model_name=model_name,
                    field_name=schema_field.name,
                ))

        return violations

    def validate_sqlite_table(
        self,
        create_table_sql: str,
        table_name: str,
        schema_model_name: str,
    ) -> list[SchemaViolation]:
        """验证 SQLite 表结构是否符合 schema。"""
        violations = []

        schema = self.registry.get_model(schema_model_name)
        if not schema:
            violations.append(SchemaViolation(
                severity="error",
                message=f"Schema 中未找到模型: {schema_model_name}",
            ))
            return violations

        actual_fields = SQLiteSchemaParser.parse_create_table_sql(create_table_sql)
        actual_field_names = {f["name"] for f in actual_fields}
        schema_field_names = {f.name for f in schema.fields}

        # 检查实际字段是否都在 schema 中
        for field_name in actual_field_names:
            if field_name not in schema_field_names:
                violations.append(SchemaViolation(
                    severity="error",
                    message=f"表字段 '{field_name}' 不在 schema 中",
                    model_name=schema_model_name,
                    field_name=field_name,
                ))

        # 检查 schema 中的必填字段是否都存在
        for schema_field in schema.fields:
            if not schema_field.optional and schema_field.name not in actual_field_names:
                violations.append(SchemaViolation(
                    severity="error",
                    message=f"表缺少 schema 中的必填字段 '{schema_field.name}'",
                    model_name=schema_model_name,
                    field_name=schema_field.name,
                ))

        return violations

    def validate_field_references_in_file(
        self,
        file_path: Path,
        model_name: str,
    ) -> list[SchemaViolation]:
        """验证文件中的字段引用是否存在于 schema。"""
        violations = []

        schema = self.registry.get_model(model_name)
        if not schema:
            violations.append(SchemaViolation(
                severity="error",
                message=f"Schema 中未找到模型: {model_name}",
                file_path=str(file_path),
            ))
            return violations

        schema_field_names = {f.name for f in schema.fields}
        content = file_path.read_text(encoding="utf-8")

        # 简单的字段引用检测（使用正则表达式）
        # 检测形如 task.field_name 或 ["field_name"] 的引用
        patterns = [
            r'\.(\w+)',  # .field_name
            r'\["(\w+)"\]',  # ["field_name"]
            r"\['(\w+)'\]",  # ['field_name']
            r':(\w+)',  # :field_name (SQL 占位符)
        ]

        lines = content.split('\n')
        for line_num, line in enumerate(lines, 1):
            for pattern in patterns:
                for match in re.finditer(pattern, line):
                    field_ref = match.group(1)
                    # 过滤掉明显不是字段的引用
                    if field_ref in ('self', 'dict', 'model', 'json', 'str', 'int', 'bool', 'datetime'):
                        continue
                    if field_ref not in schema_field_names:
                        # 这是一个警告而不是错误，因为正则可能误判
                        violations.append(SchemaViolation(
                            severity="warning",
                            message=f"可能引用了不存在的字段 '{field_ref}'",
                            file_path=str(file_path),
                            line_number=line_num,
                            model_name=model_name,
                            field_name=field_ref,
                        ))

        return violations


def validate_workspace_schemas(
    workspace_path: Path,
    registry: SchemaRegistry,
) -> list[SchemaViolation]:
    """验证整个 workspace 的 schema 一致性。"""
    violations = []
    validator = SchemaValidator(registry)

    # 验证模型定义
    models_file = workspace_path / "backend" / "app" / "models.py"
    if models_file.exists():
        for model in registry.models:
            model_violations = validator.validate_pydantic_model(
                models_file,
                model.name,
                model.name,
            )
            violations.extend(model_violations)

    # 验证仓储实现中的 SQL
    repository_file = workspace_path / "backend" / "app" / "repository.py"
    if repository_file.exists():
        content = repository_file.read_text(encoding="utf-8")
        # 提取 CREATE TABLE 语句
        create_table_pattern = r'CREATE TABLE.*?(?=\)|$)'
        for match in re.finditer(create_table_pattern, content, re.DOTALL | re.IGNORECASE):
            sql = match.group(0) + ")"
            # 假设表名与模型名对应
            for model in registry.models:
                table_name = model.name.lower() + "s"  # 简单的复数形式
                if table_name in sql.lower():
                    sql_violations = validator.validate_sqlite_table(
                        sql,
                        table_name,
                        model.name,
                    )
                    violations.extend(sql_violations)

    # 验证测试文件中的字段引用
    tests_dir = workspace_path / "tests"
    if tests_dir.exists():
        for test_file in tests_dir.glob("test_*.py"):
            for model in registry.models:
                ref_violations = validator.validate_field_references_in_file(
                    test_file,
                    model.name,
                )
                violations.extend(ref_violations)

    return violations
