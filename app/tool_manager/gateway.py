from __future__ import annotations

from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from app.tool_manager.catalog import SourceRegistration, ToolCatalog
from app.tool_manager.access_policy import ToolAccessPolicy
from app.tool_manager.crewai_adapter import ProjectOSTool
from app.tool_manager.source import ToolExposure, ToolSource
from app.execution_context import ExecutionContext
from app.tool_manager.grants import CapabilityGrant

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

    def activate_grant(self, grant: CapabilityGrant) -> int:
        """Activate a persisted grant without widening its scope."""
        activated = 0
        for domain in self._catalog.domains():
            if any(
                registration.source_name == grant.source_name
                and registration.capability == grant.capability
                for registration in self._catalog.find_sources(domain, grant.capability)
            ):
                self._policy.activate_grant(grant)
                activated += 1
        if activated == 0:
            raise ValueError(
                f"能力 '{grant.capability}' 下不存在来源 '{grant.source_name}'"
            )
        return activated

    def deactivate_grant(self, grant_id: str) -> None:
        self._policy.deactivate_grant(grant_id)

    def tools_for(
        self, domain: str, *, context: ExecutionContext | None = None
    ) -> list[BaseTool]:
        """返回当前 domain 可交给 CrewAI Agent 的已授权工具。"""
        registrations = self._catalog.list_registrations(
            domain,
            refresh_sources=self._policy.active_grant_source_names(),
        )
        return [
            ProjectOSTool.from_registration(
                registration,
                is_authorized=self._policy.is_visible,
                context=context,
            )
            for registration in registrations
            if self._policy.is_visible(registration, context)
            and _visible_in_execution_context(registration.definition, context)
            and _visible_in_tool_allowlist(registration.definition, context)
        ]

    def find_sources_for_capability(
        self, domain: str, capability: str
    ) -> list[SourceRegistration]:
        """按能力查询候选动态来源，不触发 MCP discover。"""
        return self._catalog.find_sources(domain, capability)

def _visible_in_execution_context(
    definition: "ToolDef", context: ExecutionContext | None
) -> bool:
    """模式是 Runner 发放的可信授权，不是 LLM 可选择的工具参数。"""
    if context is None or definition.execution_modes is None:
        return True
    return context.execution_mode.value in definition.execution_modes


def _visible_in_tool_allowlist(
    definition: "ToolDef", context: ExecutionContext | None
) -> bool:
    """Apply an optional control-plane narrowing for a single retry attempt."""
    if definition.name == "report_progress":
        return True
    if context is None or not context.tool_allowlist:
        return True
    return definition.name in context.tool_allowlist
