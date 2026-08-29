"""ToolGateway 使用的工具目录与来源注册记录。"""

from __future__ import annotations

from dataclasses import dataclass

from app.tool_manager.source import ToolDef, ToolExposure, ToolSource


@dataclass(frozen=True)
class ToolRegistration:
    """一个工具在系统中的注册记录。

    ToolDef 只描述工具；注册记录补全它属于哪个能力域、工具集、来源及暴露策略。
    """

    definition: ToolDef
    domain: str
    capability: str
    toolset: str
    source_name: str
    source: ToolSource
    exposure: ToolExposure

    @property
    def key(self) -> tuple[str, str]:
        return self.domain, self.definition.name


class ToolCatalog:
    """全量工具目录。

    Catalog 不决定 Agent 能看到哪些工具，也不执行工具；它只维护工具声明和
    对应的执行来源。动态来源在获授权后刷新，保证 MCP 目录不会陈旧或越权发现。
    """

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str], SourceRegistration] = {}
        self._static_tools: dict[tuple[str, str], ToolRegistration] = {}
        self._dynamic_tools: dict[tuple[str, str], ToolRegistration] = {}

    def register_source(
        self,
        *,
        domain: str,
        capability: str,
        toolset: str,
        source_name: str,
        source: ToolSource,
        exposure: ToolExposure,
    ) -> None:
        key = (domain, source_name)
        if key in self._sources:
            raise ValueError(f"来源 '{source_name}' 已注册到 domain '{domain}'")

        record = SourceRegistration(
            domain=domain,
            capability=capability,
            toolset=toolset,
            source_name=source_name,
            source=source,
            exposure=exposure,
        )
        self._sources[key] = record

        if not source.is_dynamic:
            self._register_discovered(record, source.discover(), self._static_tools)

    def list_registrations(
        self, domain: str, *, refresh_sources: set[str] | None = None
    ) -> list[ToolRegistration]:
        """返回 domain 下的目录，并只刷新已授权的动态来源。"""
        self._refresh_dynamic_sources(domain, refresh_sources or set())
        return [
            registration
            for (registered_domain, _), registration in self._all_tools().items()
            if registered_domain == domain
        ]

    def find(
        self,
        domain: str,
        tool_name: str,
        *,
        refresh_sources: set[str] | None = None,
    ) -> ToolRegistration | None:
        self._refresh_dynamic_sources(domain, refresh_sources or set())
        return self._all_tools().get((domain, tool_name))

    def find_sources(
        self,
        domain: str,
        capability: str,
        *,
        dynamic_only: bool = True,
    ) -> list[SourceRegistration]:
        """按能力查找候选来源，不触发动态来源 discover。"""
        return [
            registration
            for (registered_domain, _), registration in self._sources.items()
            if registered_domain == domain
            and registration.capability == capability
            and (not dynamic_only or registration.source.is_dynamic)
        ]

    def domains(self) -> set[str]:
        """返回所有注册了来源的 domain，用于跨 domain 的会话授权。"""
        return {registered_domain for (registered_domain, _) in self._sources}

    def _refresh_dynamic_sources(
        self, domain: str, source_names: set[str]
    ) -> None:
        for key, record in self._sources.items():
            if (
                key[0] != domain
                or record.source_name not in source_names
                or not record.source.is_dynamic
            ):
                continue

            self._remove_source_tools(self._dynamic_tools, record)
            self._register_discovered(
                record, record.source.discover(), self._dynamic_tools
            )

    def _register_discovered(
        self,
        source_record: SourceRegistration,
        definitions: list[ToolDef],
        target: dict[tuple[str, str], ToolRegistration],
    ) -> None:
        for definition in definitions:
            registration = ToolRegistration(
                definition=definition,
                domain=source_record.domain,
                capability=source_record.capability,
                toolset=source_record.toolset,
                source_name=source_record.source_name,
                source=source_record.source,
                exposure=source_record.exposure,
            )
            key = registration.key
            existing = self._all_tools().get(key)
            if existing is not None and existing.source_name != registration.source_name:
                raise ValueError(
                    f"domain '{registration.domain}' 下的工具名称冲突: "
                    f"'{definition.name}' 同时来自 "
                    f"'{existing.source_name}' 和 '{registration.source_name}'"
                )
            target[key] = registration

    @staticmethod
    def _remove_source_tools(
        tools: dict[tuple[str, str], ToolRegistration],
        source_record: SourceRegistration,
    ) -> None:
        stale_keys = [
            key
            for key, registration in tools.items()
            if registration.domain == source_record.domain
            and registration.source_name == source_record.source_name
        ]
        for key in stale_keys:
            del tools[key]

    def _all_tools(self) -> dict[tuple[str, str], ToolRegistration]:
        return {**self._static_tools, **self._dynamic_tools}


@dataclass(frozen=True)
class SourceRegistration:
    """一个来源的注册上下文，供 Workflow 选择能力提供方。"""

    domain: str
    capability: str
    toolset: str
    source_name: str
    source: ToolSource
    exposure: ToolExposure
