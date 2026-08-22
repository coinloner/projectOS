from __future__ import annotations

from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from app.tool_manager.catalog import SourceRegistration, ToolCatalog
from app.tool_manager.access_policy import ToolAccessPolicy
from app.tool_manager.crewai_adapter import ProjectOSTool
from app.tool_manager.source import ToolExposure, ToolSource
from app.execution_context import ExecutionContext

if TYPE_CHECKING:
    from app.tool_manager.source import ToolDef


class ToolGateway:
    """ProjectOS 与 CrewAI 工具运行时之间的边界。

    Gateway 不执行工具，也不实现工具调用循环。它只回答一个问题：给定 domain
    和当前授权状态，哪些 ProjectOS 工具可以被包装成 CrewAI BaseTool 交给
    领域 Agent。Catalog 管理声明与来源，AccessPolicy 管理 source 授权。
    """

    def __init__(self) -> None:
        self._catalog = ToolCatalog()
        self._policy = ToolAccessPolicy()

    def register_toolset(
        self,
        domain: str,
        name: str,
        toolset: ToolSource,
        *,
        capability: str | None = None,
        exposure: ToolExposure = ToolExposure.ALWAYS,
    ) -> None:
        """注册已知本地 ToolSet；它默认始终向对应 domain 暴露。"""
        if toolset.is_dynamic:
            raise ValueError("动态来源必须通过 register_source() 注册")
        self._catalog.register_source(
            domain=domain,
            capability=capability or domain,
            toolset=name,
            source_name=name,
            source=toolset,
            exposure=exposure,
        )

    def register_source(
        self,
        domain: str,
        name: str,
        source: ToolSource,
        *,
        capability: str | None = None,
        exposure: ToolExposure = ToolExposure.ON_DEMAND,
    ) -> None:
        """注册 MCP 等按需来源；注册本身不会发现或授权远端工具。"""
        self._catalog.register_source(
            domain=domain,
            capability=capability or domain,
            toolset=name,
            source_name=name,
            source=source,
            exposure=exposure,
        )

    def activate_source(self, domain: str, name: str) -> None:
        """授予当前会话使用一个按需来源的权限，例如 MCP。"""
        if not self._catalog.has_source(domain, name):
            raise ValueError(f"domain '{domain}' 下不存在来源 '{name}'")
        self._policy.activate_source(domain, name)

    def activate_source_for_capability(self, capability: str, name: str) -> int:
        """在提供某能力的所有 domain 下激活同名来源；返回激活的 domain 数。

        一次审批覆盖整个交付链：docs-mcp 注册于 architecture/code/review 三个
        domain，架构节点触发审批后，后续实现与审查节点也应直接可用，
        而不是每到一个 domain 就再次等待审批。
        """
        activated = 0
        for domain in self._catalog.domains():
            if any(
                registration.source_name == name
                and registration.capability == capability
                for registration in self._catalog.find_sources(domain, capability)
            ):
                self.activate_source(domain, name)
                activated += 1
        return activated

    def deactivate_source(self, domain: str, name: str) -> None:
        """撤销一个按需来源的会话授权。"""
        self._policy.deactivate_source(domain, name)

    def tools_for(
        self, domain: str, *, context: ExecutionContext | None = None
    ) -> list[BaseTool]:
        """返回当前 domain 可交给 CrewAI Agent 的已授权工具。"""
        registrations = self._catalog.list_registrations(
            domain,
            refresh_sources=self._policy.active_source_names(domain),
        )
        return [
            ProjectOSTool.from_registration(
                registration,
                is_authorized=self._policy.is_visible,
                context=context,
            )
            for registration in registrations
            if self._policy.is_visible(registration)
            and _visible_in_execution_context(registration.definition, context)
        ]

    def find_sources_for_capability(
        self, domain: str, capability: str
    ) -> list[SourceRegistration]:
        """按能力查询候选动态来源，不触发 MCP discover。"""
        return self._catalog.find_sources(domain, capability)

    # 兼容过渡：旧调用方仍可使用 domain 级外部授权，但新代码应精确授权 source。
    def enable_external(self, domain: str) -> None:
        for source_name in self._catalog.source_names(domain, dynamic_only=True):
            self.activate_source(domain, source_name)

    def activate_external(self, domain: str) -> None:
        self.enable_external(domain)

    def deactivate_external(self, domain: str) -> None:
        for source_name in self._catalog.source_names(domain, dynamic_only=True):
            self.deactivate_source(domain, source_name)


def _visible_in_execution_context(
    definition: "ToolDef", context: ExecutionContext | None
) -> bool:
    """模式是 Runner 发放的可信授权，不是 LLM 可选择的工具参数。"""
    if context is None or definition.execution_modes is None:
        return True
    return context.execution_mode.value in definition.execution_modes
