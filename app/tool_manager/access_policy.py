from __future__ import annotations

from app.tool_manager.catalog import ToolRegistration
from app.tool_manager.source import ToolExposure


class ToolAccessPolicy:
    """控制工具向 Agent 暴露的范围。

    ALWAYS 工具始终可见。ON_DEMAND 工具（典型是 MCP）必须显式激活来源。
    CONFIRM 和 DENIED 工具不进入普通 Agent 的 tool-calling 列表。
    """

    def __init__(self) -> None:
        self._active_sources: set[tuple[str, str]] = set()

    def activate_source(self, domain: str, source_name: str) -> None:
        self._active_sources.add((domain, source_name))

    def deactivate_source(self, domain: str, source_name: str) -> None:
        self._active_sources.discard((domain, source_name))

    def active_source_names(self, domain: str) -> set[str]:
        return {
            source_name
            for active_domain, source_name in self._active_sources
            if active_domain == domain
        }

    def is_visible(self, registration: ToolRegistration) -> bool:
        if registration.exposure is ToolExposure.ALWAYS:
            return True
        if registration.exposure is ToolExposure.ON_DEMAND:
            return (registration.domain, registration.source_name) in self._active_sources
        return False
