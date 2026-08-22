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
from app.workflow.templates import (
    architecture_compact_template,
    architecture_parallel_template,
    project_delivery_template,
    project_delivery_minimal_template,
)


MODULES: tuple[Callable[["ProjectOSContainer"], None], ...] = (
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
    container.templates.register(architecture_compact_template())
    container.templates.register(architecture_parallel_template())
    container.templates.register(project_delivery_template())
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
    # 外部文档能力是跨领域能力：需求（规范核实后落需求）、架构（接口契约）、
    # 实现（代码细节）与审查（规范一致性核验）都可能在交付链上请求同一来源。
    # 任一节点先发起请求都会经 activate_source_for_capability 一次性激活全部
    # 已注册 domain，后续节点直接可用，不再产生第二次等待。
    for domain in ("requirement", "architecture", "code", "review"):
        container.gateway.register_source(
            domain=domain,
            name="docs-mcp",
            source=MCPToolSource(client=StreamableHttpMCPClient(url)),
            capability="external_documentation",
        )
