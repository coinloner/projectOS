"""技术栈选择工具 - Architecture Agent 专用。

这是 Architecture Agent 声明 tech_stack 的唯一正确方式。
"""

from typing import Literal
import os


# 主流技术栈白名单
MAINSTREAM_TECH_STACKS = {
    "frontend": {
        "frameworks": ["react", "vue", "angular", "svelte", "solid"],
        "build_tools": ["vite", "webpack", "rollup", "parcel"],
        "recommended_combinations": [
            ["react", "vite"],
            ["vue", "vite"],
            ["svelte", "vite"],
            ["angular"],
        ]
    },
    "backend": {
        "frameworks": [
            "fastapi", "flask", "django",  # Python
            "express", "nestjs", "koa",     # Node.js
            "spring-boot",                  # Java
            "gin", "echo",                  # Go
        ],
        "recommended_combinations": [
            ["fastapi"],
            ["flask"],
            ["express"],
            ["django"],
        ]
    },
    "database": {
        "databases": [
            "postgresql", "mysql", "sqlite",  # SQL
            "mongodb", "redis",               # NoSQL
        ],
        "recommended_combinations": [
            ["sqlite"],
            ["postgresql"],
            ["mysql"],
            ["mongodb"],
        ]
    },
    "schema": {
        "tools": ["json-schema", "openapi", "protobuf", "graphql"],
        "recommended_combinations": [
            ["json-schema"],
            ["openapi"],
        ]
    },
    "container": {
        "tools": ["docker", "kubernetes", "podman"],
        "recommended_combinations": [
            ["docker"],
        ]
    },
}


def select_tech_stack(
    module_id: str,
    module_category: Literal["frontend", "backend", "database", "schema", "container", "other"],
    requested_stack: str | None = None
) -> dict:
    """
    从主流技术栈中选择合适的技术栈，或验证自定义技术栈。

    这是 Architecture Agent 声明 tech_stack 的**唯一正确方式**。
    在设计每个模块时，必须先调用此工具获取可用技术栈列表。

    Args:
        module_id: 模块ID，用于日志记录
        module_category: 模块类别，用于过滤相关的技术栈
        requested_stack: 可选，要验证的自定义技术栈名称

    Returns:
        {
            "available_stacks": [...],           # 可选的技术栈列表
            "recommended_combinations": [...],   # 推荐的组合
            "instruction": "使用说明"
        }
        或验证结果（当提供 requested_stack 时）

    Example:
        >>> select_tech_stack("book-api", "backend")
        {
            "available_stacks": ["fastapi", "flask", "django", ...],
            "recommended_combinations": [["fastapi"], ["flask"]],
            "instruction": "请从 available_stacks 中选择..."
        }

        >>> select_tech_stack("todo-ui", "frontend", requested_stack="vanilla-js")
        {
            "status": "approved",
            "requested_stack": "vanilla-js",
            "reason": "纯 JavaScript 是标准前端技术",
            ...
        }
    """
    category_stacks = MAINSTREAM_TECH_STACKS.get(module_category, {})

    # 收集所有可用的技术栈
    available = []
    for key, stacks in category_stacks.items():
        if key != "recommended_combinations":
            available.extend(stacks)

    # 推荐组合
    recommended = category_stacks.get("recommended_combinations", [])

    if not available:
        # 未知类别，返回所有主流技术栈
        all_stacks = []
        for cat_data in MAINSTREAM_TECH_STACKS.values():
            for key, stacks in cat_data.items():
                if key != "recommended_combinations":
                    all_stacks.extend(stacks)

        available = sorted(set(all_stacks))
        recommended = []

    # 如果没有请求验证特定技术栈，返回可用列表
    if not requested_stack:
        return {
            "module_id": module_id,
            "category": module_category,
            "available_stacks": sorted(set(available)),
            "recommended_combinations": recommended,
            "instruction": (
                "请从 available_stacks 中选择技术栈，或直接使用 recommended_combinations 中的推荐组合。"
                "\n注意："
                "\n- 只选择核心技术（框架、工具、数据库），不要选择编程语言"
                "\n- 前端模块通常需要：框架 + 构建工具"
                "\n- 后端模块通常只需要：框架"
                "\n- 如果列表中没有你需要的技术栈，可以通过 requested_stack 参数请求验证自定义技术栈"
            )
        }

    # 检查是否在白名单中
    if requested_stack.lower() in [s.lower() for s in available]:
        return {
            "module_id": module_id,
            "category": module_category,
            "requested_stack": requested_stack,
            "status": "approved",
            "reason": "技术栈在白名单中",
            "is_mainstream": True
        }

    # 不在白名单，使用 LLM 验证
    llm_validation_enabled = os.environ.get("PROJECTOS_LLM_TECH_VALIDATION", "1") == "1"

    if not llm_validation_enabled:
        return {
            "module_id": module_id,
            "category": module_category,
            "requested_stack": requested_stack,
            "status": "rejected",
            "reason": "技术栈不在白名单中，且 LLM 验证已禁用",
            "available_stacks": sorted(set(available)),
            "is_mainstream": False
        }

    # 使用 LLM 验证器
    try:
        from app.domain.architecture.capability_validator import CapabilityValidator

        validator = CapabilityValidator()
        result = validator.validate_tech_stack(
            stack_name=requested_stack,
            category=module_category,
            context=f"模块 {module_id} 的 {module_category} 技术栈"
        )

        return {
            "module_id": module_id,
            "category": module_category,
            "requested_stack": requested_stack,
            "status": "approved" if result.is_valid else "rejected",
            "reason": result.reason,
            "confidence": result.confidence,
            "validation_details": result.metadata,
            "is_mainstream": False
        }

    except Exception as e:
        # 验证失败时降级到拒绝
        return {
            "module_id": module_id,
            "category": module_category,
            "requested_stack": requested_stack,
            "status": "rejected",
            "reason": f"验证过程出错: {str(e)}",
            "available_stacks": sorted(set(available)),
            "is_mainstream": False
        }


def get_all_mainstream_stacks() -> list[str]:
    """获取所有主流技术栈列表（用于验证）。"""
    all_stacks = []
    for cat_data in MAINSTREAM_TECH_STACKS.values():
        for key, stacks in cat_data.items():
            if key != "recommended_combinations":
                all_stacks.extend(stacks)
    return sorted(set(all_stacks))


# 导出主流技术栈列表（供验证器使用）
ALL_MAINSTREAM_STACKS = get_all_mainstream_stacks()


__all__ = [
    "select_tech_stack",
    "get_all_mainstream_stacks",
    "ALL_MAINSTREAM_STACKS",
    "MAINSTREAM_TECH_STACKS",
]
