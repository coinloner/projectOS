"""Schema 验证工具，供 Integration Agent 使用。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.orchestration.schema_registry import SchemaRegistry
from app.orchestration.schema_validator import validate_workspace_schemas

logger = logging.getLogger(__name__)


def validate_schemas_in_workspace(
    workspace_path: Path,
    schemas_json_path: Path | None = None,
) -> dict[str, Any]:
    """
    验证 workspace 中的代码是否符合 Schema Registry。

    返回格式:
    {
        "has_violations": bool,
        "violations": [
            {
                "severity": "error" | "warning",
                "message": str,
                "file_path": str | None,
                "line_number": int | None,
                "model_name": str | None,
                "field_name": str | None,
            }
        ],
        "summary": str,
    }
    """
    result = {
        "has_violations": False,
        "violations": [],
        "summary": "未找到 Schema Registry，跳过验证",
    }

    # 查找 schemas.json
    if schemas_json_path is None:
        # 尝试在 workspace 的各个位置查找
        possible_paths = [
            workspace_path / "schemas.json",
            workspace_path / ".projectos" / "schemas.json",
            workspace_path / "backend" / "schemas.json",
        ]
        for path in possible_paths:
            if path.exists():
                schemas_json_path = path
                break

    if schemas_json_path is None or not schemas_json_path.exists():
        logger.info("未找到 Schema Registry，跳过 schema 验证")
        return result

    try:
        # 加载 Schema Registry
        registry = SchemaRegistry.load_from_file(schemas_json_path)
        logger.info(f"加载 Schema Registry: {len(registry.models)} 个模型")

        # 执行验证
        violations = validate_workspace_schemas(workspace_path, registry)

        # 转换为字典格式
        violations_list = []
        for v in violations:
            violations_list.append({
                "severity": v.severity,
                "message": v.message,
                "file_path": v.file_path,
                "line_number": v.line_number,
                "model_name": v.model_name,
                "field_name": v.field_name,
            })

        # 统计错误和警告
        errors = [v for v in violations if v.severity == "error"]
        warnings = [v for v in violations if v.severity == "warning"]

        result["has_violations"] = len(errors) > 0
        result["violations"] = violations_list
        result["summary"] = (
            f"Schema 验证完成: {len(errors)} 个错误, {len(warnings)} 个警告。"
            + (f" 发现 {len(errors)} 个阻塞性问题，不能合并。" if errors else " 可以合并。")
        )

        if errors:
            logger.warning(f"Schema 验证失败: {len(errors)} 个错误")
            for error in errors[:5]:  # 只记录前 5 个
                logger.warning(f"  - {error.message}")
        else:
            logger.info("Schema 验证通过")

    except Exception as e:
        logger.error(f"Schema 验证过程出错: {e}", exc_info=True)
        result["has_violations"] = False
        result["violations"] = []
        result["summary"] = f"Schema 验证过程出错: {e}"

    return result


def format_schema_violations_for_review(violations: list[dict[str, Any]]) -> str:
    """格式化 schema 违规信息，供审查员阅读。"""
    if not violations:
        return "✅ Schema 验证通过，所有字段引用与定义一致。"

    errors = [v for v in violations if v["severity"] == "error"]
    warnings = [v for v in violations if v["severity"] == "warning"]

    lines = []
    lines.append(f"❌ Schema 验证发现 {len(errors)} 个错误, {len(warnings)} 个警告:\n")

    if errors:
        lines.append("**错误 (阻塞合并):**")
        for i, error in enumerate(errors[:10], 1):  # 最多显示 10 个
            location = ""
            if error.get("file_path"):
                location = f" ({error['file_path']}"
                if error.get("line_number"):
                    location += f":{error['line_number']}"
                location += ")"
            lines.append(f"{i}. {error['message']}{location}")
        if len(errors) > 10:
            lines.append(f"   ... 还有 {len(errors) - 10} 个错误")
        lines.append("")

    if warnings:
        lines.append("**警告 (不阻塞合并):**")
        for i, warning in enumerate(warnings[:5], 1):  # 最多显示 5 个
            location = ""
            if warning.get("file_path"):
                location = f" ({warning['file_path']}"
                if warning.get("line_number"):
                    location += f":{warning['line_number']}"
                location += ")"
            lines.append(f"{i}. {warning['message']}{location}")
        if len(warnings) > 5:
            lines.append(f"   ... 还有 {len(warnings) - 5} 个警告")

    return "\n".join(lines)
