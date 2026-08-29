from __future__ import annotations

from typing import TYPE_CHECKING

from app.tool_manager.catalog import ToolRegistration
from app.tool_manager.source import ToolExposure

if TYPE_CHECKING:
    from app.execution_context import ExecutionContext
    from app.tool_manager.grants import CapabilityGrant


class ToolAccessPolicy:
    """控制工具向 Agent 暴露的范围。

    ALWAYS 工具始终可见。ON_DEMAND 工具（典型是 MCP）必须显式激活来源。
    CONFIRM 和 DENIED 工具不进入普通 Agent 的 tool-calling 列表。
    """

    def __init__(self) -> None:
        self._active_grants: dict[str, CapabilityGrant] = {}

    def activate_grant(self, grant: "CapabilityGrant") -> None:
        if grant.is_active():
            self._active_grants[grant.grant_id] = grant

    def deactivate_grant(self, grant_id: str) -> None:
        self._active_grants.pop(grant_id, None)

    def active_grant_source_names(self) -> set[str]:
        return {
            grant.source_name
            for grant in self._active_grants.values()
            if grant.is_active()
        }

    def is_visible(
        self,
        registration: ToolRegistration,
        context: "ExecutionContext | None" = None,
    ) -> bool:
        if registration.exposure is ToolExposure.ALWAYS:
            return True
        if registration.exposure is ToolExposure.ON_DEMAND:
            return any(
                self._grant_matches(registration, grant, context)
                for grant in self._active_grants.values()
            )
        return False

    @staticmethod
    def _grant_matches(
        registration: ToolRegistration,
        grant: "CapabilityGrant",
        context: "ExecutionContext | None",
    ) -> bool:
        if not grant.is_active():
            return False
        if (
            registration.capability != grant.capability
            or registration.source_name != grant.source_name
        ):
            return False
        # Scoped grants must never be exposed without a trusted execution identity.
        if context is None:
            return False
        if grant.scope == "project":
            return True
        if grant.trace_id != context.trace_id:
            return False
        if grant.scope == "trace":
            return True
        return (
            grant.scope == "node"
            and grant.work_item_id is not None
            and grant.work_item_id == context.work_item_id
        )
