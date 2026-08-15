"""兼容旧的 ``ToolManager`` 导入路径。

新的正式名称是 ``ToolGateway``，因为这一层不再管理工具调用执行，而是向
CrewAI 暴露已经过 ProjectOS 目录与授权筛选的工具。
"""

from app.tool_manager.gateway import ToolGateway

ToolManager = ToolGateway

__all__ = ["ToolManager"]
