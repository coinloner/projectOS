"""Domain 模块安装清单。

这里是唯一的运行时模块组合位置；main.py、FastAPI 和测试不再逐个注册 Agent/Tool。
"""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer

from app.domain.architecture.module import install as install_architecture
from app.domain.bootstrap.module import install as install_bootstrap
from app.domain.code.module import install as install_code
from app.domain.requirement.module import install as install_requirement
from app.domain.review.module import install as install_review
from app.domain.task.module import install as install_task
from app.domain.test.module import install as install_test
from app.policy.module import install as install_policy
from app.skill.module import install as install_skill
from app.workflow.templates import (
    architecture_layered_template,
    architecture_compact_template,
    architecture_parallel_template,
    project_delivery_template,
    project_delivery_dynamic_template,
    project_delivery_layered_template,
    project_delivery_minimal_template,
)


MODULES: tuple[Callable[["ProjectOSContainer"], None], ...] = (
    install_policy,
    install_skill,
    install_requirement,
    install_architecture,
    install_task,
    install_bootstrap,
    install_code,
    install_test,
    install_review,
)


def install_modules(container: "ProjectOSContainer") -> None:
    for install in MODULES:
        install(container)
    from app.orchestration.progress_tools import register_progress_tools

    register_progress_tools(
        container.gateway,
        tuple(definition.domain for definition in container.agents.definitions()),
    )
    container.templates.register(architecture_compact_template())
    container.templates.register(architecture_parallel_template())
    container.templates.register(architecture_layered_template())
    container.templates.register(project_delivery_template())
    container.templates.register(project_delivery_dynamic_template())
    container.templates.register(project_delivery_layered_template())
    container.templates.register(project_delivery_minimal_template())
    _install_external_sources(container)


def _install_external_sources(container: "ProjectOSContainer") -> None:
    """注册按需动态来源。

    docs-mcp 是演示用外部文档服务（app.demo.docs_mcp_server），默认在
    ON_DEMAND 暴露：Agent 发出 external_documentation 能力请求、项目所有者
    通过 capabilities/approve 批准后，工具才会被发现并可用。
    """
    from app.tool_manager.mcp_http import StreamableHttpMCPClient
    from app.tool_manager.source import MCPToolSource

    url = os.environ.get(
        "PROJECTOS_DOCS_MCP_URL", "http://127.0.0.1:8090/mcp"
    )
    # 外部文档能力可由审批授予 node、trace 或 project scope；Gateway 在
    # 每次节点构造工具和执行工具时按 ExecutionContext 做最终隔离。
    for domain in ("requirement", "architecture", "code", "review"):
        container.gateway.register_source(
            domain=domain,
            name="docs-mcp",
            source=MCPToolSource(client=StreamableHttpMCPClient(url)),
            capability="external_documentation",
        )
