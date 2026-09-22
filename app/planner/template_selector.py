"""模板选择逻辑 - 根据项目状态和用户意图选择合适的模板。"""

import os
from pathlib import Path


def select_template(project_path: str, goal: str) -> str:
    """根据项目状态和用户意图选择合适的交付模板。

    选择逻辑:
    1. 如果检测到已有 project-contract.json → delivery_incremental
    2. 如果明确请求只要架构 → architecture_only
    3. 默认 → delivery_default

    Args:
        project_path: 项目路径
        goal: 用户目标描述

    Returns:
        模板 ID
    """
    # 检测是否已有 project-contract.json (架构基线已确定)
    contract_path = Path(project_path) / ".projectos" / "architecture" / "project-contract.json"
    if contract_path.exists():
        return "delivery_incremental"

    # 检测是否明确请求只要架构设计
    goal_lower = goal.lower()
    architecture_only_keywords = [
        "只要架构",
        "仅架构",
        "只需要架构",
        "architecture only",
        "design only",
        "只做架构设计",
        "不要实现",
        "不需要代码",
    ]
    if any(keyword in goal_lower for keyword in architecture_only_keywords):
        return "architecture_only"

    # 默认:完整交付流程
    return "delivery_default"
