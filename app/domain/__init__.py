"""ProjectOS 领域工具合同。

每个子目录对应一个 Agent domain；其 ``tools.py`` 是该 Agent 可见工具、
产物读写边界和 ToolGateway 注册的唯一声明位置。
"""

from app.domain.architecture.tools import register_architecture_tools
from app.domain.bootstrap.tools import register_bootstrap_tools
from app.domain.code.tools import register_code_tools
from app.domain.requirement.tools import register_requirement_tools
from app.domain.review.tools import register_review_tools
from app.domain.task.tools import register_task_tools
from app.domain.test.tools import register_test_tools

__all__ = [
    "register_architecture_tools",
    "register_bootstrap_tools",
    "register_code_tools",
    "register_requirement_tools",
    "register_review_tools",
    "register_task_tools",
    "register_test_tools",
]
