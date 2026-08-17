from __future__ import annotations

from crewai.tools import BaseTool

from app.tool_manager.catalog import SourceRegistration, ToolCatalog
from app.tool_manager.access_policy import ToolAccessPolicy
from app.tool_manager.crewai_adapter import ProjectOSTool
from app.tool_manager.source import ToolExposure, ToolSource
from app.execution_context import ExecutionContext


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
