"""技术栈选择工具 - Architecture Agent 专用。

这是 Architecture Agent 声明 tech_stack 的唯一正确方式。
"""

from typing import Literal


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
    module_category: Literal["frontend", "backend", "database", "schema", "container", "other"]
) -> dict:
    """
    从主流技术栈中选择合适的技术栈。

    这是 Architecture Agent 声明 tech_stack 的**唯一正确方式**。
    在设计每个模块时，必须先调用此工具获取可用技术栈列表。

    Args:
        module_id: 模块ID，用于日志记录
        module_category: 模块类别，用于过滤相关的技术栈

    Returns:
        {
            "available_stacks": [...],           # 可选的技术栈列表
            "recommended_combinations": [...],   # 推荐的组合
            "instruction": "使用说明"
        }

    Example:
        >>> select_tech_stack("book-api", "backend")
        {
            "available_stacks": ["fastapi", "flask", "django", ...],
            "recommended_combinations": [["fastapi"], ["flask"]],
            "instruction": "请从 available_stacks 中选择..."
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
            "\n- 如果列表中没有你需要的技术栈，说明理由后可以在重试中使用自定义技术栈"
        )
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
