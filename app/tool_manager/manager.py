import json
from typing import Optional

from app.tool_registry.registry import ToolRegistry
from app.tool_manager.source import ToolSource


class ToolManager:
    """工具管理器 —— 控制 Agent 的工具暴露半径。

    本地工具以 ToolSet 为单位注册，默认暴露给对应 domain 的 Agent。
    外部工具通过 external source 动态发现，默认锁住，显式启用后才暴露。
    """

    def __init__(self) -> None:
        # domain -> ToolRegistry（本地 ToolSet 的持久存储）
        self._registries: dict[str, ToolRegistry] = {}

        # domain -> toolset_name -> ToolSource
        self._toolsets: dict[str, dict[str, ToolSource]] = {}

        # "domain:toolset_name" 已加载标记
        self._loaded_toolsets: set[str] = set()

        # domain -> source_name -> ToolSource
        self._external_sources: dict[str, dict[str, ToolSource]] = {}

        # external 是带锁的动态层
        self._external_enabled: set[str] = set()
        self._external_active: set[str] = set()
        self._external_cache: dict[str, dict[str, ToolSource]] = {}

    # ── 注册 ──────────────────────────────────

    def register_toolset(
        self,
        domain: str,
        name: str,
        source: ToolSource,
    ) -> None:
        """注册一个本地 ToolSet。

        本地 ToolSet 是已知能力，注册后默认暴露给对应 domain。
        ToolManager 在首次 list_tools(domain) 时懒加载。
        """
        self._toolsets.setdefault(domain, {})[name] = source

    def register_source(
        self,
        domain: str,
        name: str,
        source: ToolSource,
    ) -> None:
        """注册一个 external source。

        该方法用于 MCP 等外部动态来源。注册不等于启用，
        必须显式 enable_external() + activate_external() 后才会 discover。
        """
        self._external_sources.setdefault(domain, {})[name] = source

    # ── External 控制 ─────────────────────────

    def enable_external(self, domain: str) -> None:
        """允许某个 domain 使用 external 来源。"""
        self._external_enabled.add(domain)
        print(f"  [ToolManager] {domain}: external 已启用")

    def activate_external(self, domain: str) -> None:
        """激活某个 domain 的 external 来源。"""
        if domain not in self._external_enabled:
            raise PermissionError(
                f"domain '{domain}' 未启用 external，"
                f"请先调用 manager.enable_external('{domain}')"
            )

        self._external_active.add(domain)
        print(f"  [ToolManager] {domain}: external 已激活")

    def deactivate_external(self, domain: str) -> None:
        """关闭某个 domain 的 external 来源，并清理临时缓存。"""
        self._external_active.discard(domain)
        self._external_cache.pop(domain, None)
        print(f"  [ToolManager] {domain}: external 已关闭")

    # ── Agent 接口 ─────────────────────────────

    def list_tools(self, domain: str) -> Optional[list[dict]]:
        """返回当前允许暴露给 domain Agent 的工具列表."""
        self._ensure_toolsets_loaded(domain)

        tools: list[dict] = []
        registry = self._registries.get(domain)
        if registry is not None:
            persisted = registry.list_tools()
            if persisted is not None:
                tools.extend(persisted)

        if domain in self._external_active:
            tools.extend(self._discover_external(domain))

        return tools or None

    def call(self, name: str, arguments: str, domain: str) -> str:
        """执行指定 domain 下的工具。"""
        ext_cache = self._external_cache.get(domain, {})
        if name in ext_cache:
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                return ext_cache[name].execute(name, args)
            except Exception as e:
                return f"Error: 工具 '{name}' 执行失败: {e}"

        registry = self._registries.get(domain)
        if registry is None:
            return f"Error: 未知工具 '{name}'（domain '{domain}' 下无已注册工具）"

        return registry.call(name, arguments)

    # ── 内部 ──────────────────────────────────

    def _ensure_toolsets_loaded(self, domain: str) -> None:
        for name, source in self._toolsets.get(domain, {}).items():
            load_key = f"{domain}:{name}"
            if load_key in self._loaded_toolsets:
                continue

            registry = self._registries.setdefault(domain, ToolRegistry())
            loaded_count = 0
            for td in source.discover():
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.parameters,
                    },
                }
                registry.register(name=td.name, tool_def=tool_def, source=source)
                loaded_count += 1

            self._loaded_toolsets.add(load_key)
            if loaded_count > 0:
                print(
                    f"  [ToolManager] {domain}:{name} "
                    f"→ 注册 {loaded_count} 个工具"
                )

    def _discover_external(self, domain: str) -> list[dict]:
        self._external_cache.pop(domain, None)
        cache: dict[str, ToolSource] = {}
        tools: list[dict] = []

        for source in self._external_sources.get(domain, {}).values():
            for td in source.discover():
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": td.name,
                            "description": td.description,
                            "parameters": td.parameters,
                        },
                    }
                )
                cache[td.name] = source

        if cache:
            self._external_cache[domain] = cache
            print(
                f"  [ToolManager] {domain}:external "
                f"→ 发现 {len(cache)} 个临时工具"
            )

        return tools
