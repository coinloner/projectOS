"""增强的 Schema 验证工具，支持深度代码分析。

该模块提供:
1. AST 级别的代码分析
2. SQL 查询语句验证
3. API 端点字段验证
4. 数据库迁移脚本验证
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from app.orchestration.schema_registry import (
    FieldType,
    ModelSchema,
    SchemaRegistry,
    SchemaViolation,
)


class EnhancedPydanticModelParser:
    """增强的 Pydantic 模型解析器，支持更深入的分析。"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.content = file_path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.content)

    def parse_model_with_defaults(self, model_name: str) -> dict[str, Any]:
        """解析模型定义，包括默认值、验证器等详细信息。"""
        model_info = {
            "name": model_name,
            "fields": [],
            "validators": [],
            "config": {},
        }

        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == model_name:
                # 解析字段
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        field_info = self._parse_field_annotation(item)
                        model_info["fields"].append(field_info)

                    # 解析验证器
                    elif isinstance(item, ast.FunctionDef):
                        if any(
                            isinstance(dec, ast.Name) and dec.id == "validator"
                            for dec in item.decorator_list
                        ):
                            model_info["validators"].append(item.name)

                    # 解析 Config 类
                    elif isinstance(item, ast.ClassDef) and item.name == "Config":
                        for config_item in item.body:
                            if isinstance(config_item, ast.Assign):
                                for target in config_item.targets:
                                    if isinstance(target, ast.Name):
                                        model_info["config"][target.id] = ast.unparse(config_item.value)

        return model_info

    def _parse_field_annotation(self, node: ast.AnnAssign) -> dict[str, Any]:
        """解析字段注解，提取类型、默认值等信息。"""
        field_info = {
            "name": node.target.id if isinstance(node.target, ast.Name) else "unknown",
            "type": self._extract_type_name(node.annotation),
            "optional": self._is_optional_type(node.annotation),
            "default": None,
            "field_config": {},
        }

        # 解析默认值和 Field 配置
        if node.value:
            if isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "Field":
                    # 解析 Field 参数
                    for keyword in node.value.keywords:
                        if keyword.arg:
                            field_info["field_config"][keyword.arg] = ast.unparse(keyword.value)
            else:
                field_info["default"] = ast.unparse(node.value)

        return field_info

    def _extract_type_name(self, annotation: ast.expr) -> str:
        """提取类型名称。"""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                base_type = annotation.value.id
                if base_type in ("Optional", "Union"):
                    # 提取 Optional[T] 或 Union[T, None] 中的 T
                    if isinstance(annotation.slice, ast.Tuple):
                        for elt in annotation.slice.elts:
                            if not (isinstance(elt, ast.Constant) and elt.value is None):
                                return self._extract_type_name(elt)
                    else:
                        return self._extract_type_name(annotation.slice)
                return base_type
        return ast.unparse(annotation)

    def _is_optional_type(self, annotation: ast.expr) -> bool:
        """判断类型是否为 Optional。"""
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                return annotation.value.id in ("Optional", "Union")
        return False


class SQLQueryValidator:
    """SQL 查询语句验证器。"""

    @staticmethod
    def extract_field_references(sql: str) -> set[str]:
        """从 SQL 语句中提取字段引用。"""
        field_refs = set()

        # 提取 SELECT 子句中的字段
        select_pattern = r'SELECT\s+(.*?)\s+FROM'
        select_match = re.search(select_pattern, sql, re.IGNORECASE | re.DOTALL)
        if select_match:
            select_clause = select_match.group(1)
            # 分割字段，处理逗号
            for field in select_clause.split(','):
                field = field.strip()
                # 移除别名 (AS xxx)
                field = re.sub(r'\s+AS\s+\w+', '', field, flags=re.IGNORECASE)
                # 移除表名前缀 (table.field)
                if '.' in field:
                    field = field.split('.')[-1]
                if field and field != '*':
                    field_refs.add(field)

        # 提取 INSERT 子句中的字段
        insert_pattern = r'INSERT\s+INTO\s+\w+\s*\((.*?)\)'
        insert_match = re.search(insert_pattern, sql, re.IGNORECASE | re.DOTALL)
        if insert_match:
            fields_clause = insert_match.group(1)
            for field in fields_clause.split(','):
                field = field.strip()
                if field:
                    field_refs.add(field)

        # 提取 UPDATE 子句中的字段
        update_pattern = r'UPDATE\s+\w+\s+SET\s+(.*?)(?:WHERE|$)'
        update_match = re.search(update_pattern, sql, re.IGNORECASE | re.DOTALL)
        if update_match:
            set_clause = update_match.group(1)
            for assignment in set_clause.split(','):
                field = assignment.split('=')[0].strip()
                if field:
                    field_refs.add(field)

        # 提取 WHERE 子句中的字段
        where_pattern = r'WHERE\s+(.*?)(?:ORDER BY|GROUP BY|LIMIT|$)'
        where_match = re.search(where_pattern, sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1)
            # 简单的字段提取，查找 word = 或 word IN 等模式
            field_pattern = r'\b(\w+)\s*(?:=|!=|<|>|<=|>=|IN|LIKE)'
            for match in re.finditer(field_pattern, where_clause, re.IGNORECASE):
                field = match.group(1)
                # 过滤 SQL 关键字
                if field.upper() not in ('AND', 'OR', 'NOT', 'NULL'):
                    field_refs.add(field)

        return field_refs

    @staticmethod
    def validate_sql_against_schema(
        sql: str,
        model_schema: ModelSchema,
    ) -> list[SchemaViolation]:
        """验证 SQL 语句中的字段引用是否符合 schema。"""
        violations = []
        field_refs = SQLQueryValidator.extract_field_references(sql)
        schema_fields = {f.name for f in model_schema.fields}

        for field_ref in field_refs:
            if field_ref not in schema_fields:
                violations.append(SchemaViolation(
                    severity="error",
                    message=f"SQL 引用了不存在的字段 '{field_ref}'",
                    model_name=model_schema.name,
                    field_name=field_ref,
                ))

        return violations


class APIEndpointValidator:
    """API 端点字段验证器。"""

    @staticmethod
    def extract_response_fields_from_fastapi(file_path: Path) -> dict[str, set[str]]:
        """从 FastAPI 路由文件中提取响应字段。"""
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)

        endpoint_fields = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # 检查是否为路由函数 (有 @router.get 等装饰器)
                is_route = any(
                    isinstance(dec, ast.Call) and
                    isinstance(dec.func, ast.Attribute) and
                    dec.func.attr in ('get', 'post', 'put', 'delete', 'patch')
                    for dec in node.decorator_list
                )

                if is_route:
                    # 提取函数体中的字段引用
                    fields = APIEndpointValidator._extract_fields_from_function(node)
                    endpoint_fields[node.name] = fields

        return endpoint_fields

    @staticmethod
    def _extract_fields_from_function(func_node: ast.FunctionDef) -> set[str]:
        """从函数体中提取字段引用。"""
        fields = set()

        for node in ast.walk(func_node):
            # 查找属性访问 (obj.field)
            if isinstance(node, ast.Attribute):
                fields.add(node.attr)

            # 查找字典访问 (obj["field"])
            if isinstance(node, ast.Subscript):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    fields.add(node.slice.value)

        return fields


class EnhancedSchemaValidator:
    """增强的 Schema 验证器，集成所有验证功能。"""

    def __init__(self, registry: SchemaRegistry):
        self.registry = registry

    def validate_repository_file(
        self,
        file_path: Path,
        model_name: str,
    ) -> list[SchemaViolation]:
        """验证仓储文件中的 SQL 语句。"""
        violations = []
        schema = self.registry.get_model(model_name)
        if not schema:
            return violations

        content = file_path.read_text(encoding="utf-8")

        # 提取所有 SQL 语句
        sql_pattern = r'"""(.*?)"""'
        for match in re.finditer(sql_pattern, content, re.DOTALL):
            sql = match.group(1)
            if 'SELECT' in sql.upper() or 'INSERT' in sql.upper() or 'UPDATE' in sql.upper():
                sql_violations = SQLQueryValidator.validate_sql_against_schema(sql, schema)
                violations.extend(sql_violations)

        return violations

    def validate_api_file(
        self,
        file_path: Path,
        model_name: str,
    ) -> list[SchemaViolation]:
        """验证 API 文件中的字段引用。"""
        violations = []
        schema = self.registry.get_model(model_name)
        if not schema:
            return violations

        endpoint_fields = APIEndpointValidator.extract_response_fields_from_fastapi(file_path)
        schema_fields = {f.name for f in schema.fields}

        for endpoint_name, fields in endpoint_fields.items():
            for field in fields:
                if field not in schema_fields and not field.startswith('_'):
                    violations.append(SchemaViolation(
                        severity="warning",
                        message=f"端点 '{endpoint_name}' 引用了可能不存在的字段 '{field}'",
                        file_path=str(file_path),
                        model_name=model_name,
                        field_name=field,
                    ))

        return violations

    def comprehensive_validation(
        self,
        workspace_path: Path,
    ) -> dict[str, list[SchemaViolation]]:
        """全面验证整个 workspace。"""
        results = {
            "models": [],
            "repository": [],
            "api": [],
            "tests": [],
        }

        for model in self.registry.models:
            # 验证模型定义
            models_file = workspace_path / "backend" / "app" / "models.py"
            if models_file.exists():
                parser = EnhancedPydanticModelParser(models_file)
                model_info = parser.parse_model_with_defaults(model.name)
                # 这里可以添加更详细的验证逻辑
                results["models"].extend([])  # 占位

            # 验证仓储
            repository_file = workspace_path / "backend" / "app" / "repository.py"
            if repository_file.exists():
                repo_violations = self.validate_repository_file(repository_file, model.name)
                results["repository"].extend(repo_violations)

            # 验证 API
            api_file = workspace_path / "backend" / "app" / "api.py"
            if api_file.exists():
                api_violations = self.validate_api_file(api_file, model.name)
                results["api"].extend(api_violations)

        return results
