from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.agent.result import AgentResult
from app.execution_context import ExecutionContext


class AgentRunner(Protocol):
    """GraphRunner 需要的最小 Agent 运行契约。"""

    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        ...


AgentFactory = Callable[[], AgentRunner]


@dataclass(frozen=True)
class AgentDefinition:
    """可被 Planner 选择的领域 Agent 描述。

    factory 不属于该定义，避免 Planner 读取到可执行对象；它只保存在 Registry 内部。
    """

    id: str
    domain: str
    description: str
    output_key: str

    def __post_init__(self) -> None:
        for field_name in ("id", "domain", "description", "output_key"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"AgentDefinition.{field_name} 不能为空")


class AgentRegistry:
    """已注册领域 Agent 的受控目录。

    Planner 将来只能引用 definitions() 返回的 id。GraphRunner 使用 create() 创建
    对应实例，因此计划数据中不会携带 Python 类或工厂函数。
    """

    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._factories: dict[str, AgentFactory] = {}

    def register(self, definition: AgentDefinition, factory: AgentFactory) -> None:
        if definition.id in self._definitions:
            raise ValueError(f"Agent '{definition.id}' 已注册")
        existing = next(
            (
                registered
                for registered in self._definitions.values()
                if registered.output_key == definition.output_key
            ),
            None,
        )
        if existing is not None:
            raise ValueError(
                f"Agent '{definition.id}' 的 output_key '{definition.output_key}' "
                f"已被 '{existing.id}' 使用"
            )
        self._definitions[definition.id] = definition
        self._factories[definition.id] = factory

    def definition(self, agent_id: str) -> AgentDefinition | None:
        """返回单个可公开给 Planner 的 Agent 描述。"""
        return self._definitions.get(agent_id)

    def definitions(self) -> tuple[AgentDefinition, ...]:
        """返回 Planner 可选择的 Agent 描述，按注册顺序稳定输出。"""
        return tuple(self._definitions.values())

    def create(self, agent_id: str) -> AgentRunner:
        """仅由未来 GraphRunner 调用工厂，创建领域 Agent 实例。"""
        factory = self._factories.get(agent_id)
        if factory is None:
            raise KeyError(f"未注册 Agent: '{agent_id}'")
        return factory()
