"""接口类型选择工具 - Architecture Agent 专用。

这是 Architecture Agent 声明 interface kind 的唯一正确方式。
"""

from typing import Literal


# 接口类型白名单（只有这 5 种合法值）
VALID_INTERFACE_KINDS = {
    "symbol": {
        "description": "代码符号（函数、类、模块）",
        "examples": [
            "Python 函数/类",
            "TypeScript 函数/类",
            "可 import 的模块",
            "工具函数库",
        ],
        "consumption": "import_code",
    },
    "service": {
        "description": "独立服务或进程",
        "examples": [
            "前端应用（React+Vite）",
            "数据库（PostgreSQL）",
            "Repository 层",
            "独立微服务",
        ],
        "consumption": "http_call 或 process_spawn",
    },
    "api": {
        "description": "HTTP API 端点",
        "examples": [
            "REST API 端点",
            "GraphQL 查询",
            "HTTP 路由",
        ],
        "consumption": "http_call",
    },
    "data": {
        "description": "数据定义或 Schema",
        "examples": [
            "JSON Schema",
            "数据模型定义",
            "OpenAPI spec",
            "数据库表结构",
        ],
        "consumption": "shared_schema",
    },
    "event": {
        "description": "事件或消息",
        "examples": [
            "消息队列主题",
            "事件总线事件",
            "WebSocket 消息",
        ],
        "consumption": "http_call 或 process_spawn",
    },
}


def select_interface_kind(
    interface_name: str,
    interface_description: str,
) -> dict:
    """
    选择接口的类型标签。

    这是 Architecture Agent 声明 interface kind 的**唯一正确方式**。
    在设计每个接口时，必须先调用此工具获取可用的类型列表。

    Args:
        interface_name: 接口名称（用于日志）
        interface_description: 接口的简短描述（帮助选择正确的类型）

    Returns:
        {
            "available_kinds": {...},  # 所有可用的接口类型及其说明
            "recommendation": "...",    # 基于描述的推荐类型
            "instruction": "使用说明"
        }

    Example:
        >>> select_interface_kind("book-api.crud", "提供书籍的 CRUD HTTP 端点")
        {
            "available_kinds": {...},
            "recommendation": "api",
            "instruction": "..."
        }
    """
    # 基于描述推断推荐类型
    desc_lower = interface_description.lower()
    recommendation = None

    if any(word in desc_lower for word in ["api", "endpoint", "rest", "http", "路由"]):
        recommendation = "api"
    elif any(word in desc_lower for word in ["schema", "数据定义", "模型定义", "json schema"]):
        recommendation = "data"
    elif any(word in desc_lower for word in ["函数", "类", "模块", "function", "class", "import"]):
        recommendation = "symbol"
    elif any(word in desc_lower for word in ["服务", "应用", "前端", "数据库", "service", "app"]):
        recommendation = "service"
    elif any(word in desc_lower for word in ["事件", "消息", "event", "message"]):
        recommendation = "event"

    return {
        "interface_name": interface_name,
        "interface_description": interface_description,
        "available_kinds": VALID_INTERFACE_KINDS,
        "recommendation": recommendation,
        "instruction": (
            f"接口 '{interface_name}' 必须从以下 5 种类型中选择一种：\n"
            f"- symbol: 代码符号（函数、类、模块），通过 import 使用\n"
            f"- service: 独立服务或进程（前端应用、数据库、Repository）\n"
            f"- api: HTTP API 端点（REST、GraphQL）\n"
            f"- data: 数据定义或 Schema（JSON Schema、数据模型）\n"
            f"- event: 事件或消息（消息队列、事件总线）\n"
            f"\n基于描述 '{interface_description}'，"
            f"推荐使用: {recommendation or '无法推断，请根据实际情况选择'}\n"
            f"\n注意：不要创造新的类型名称（如 python_module, http_api, json_schema），"
            f"必须从上述 5 种中选择。"
        )
    }


__all__ = [
    "select_interface_kind",
    "VALID_INTERFACE_KINDS",
]
