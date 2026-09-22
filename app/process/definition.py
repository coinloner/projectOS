"""与具体项目结构无关的流程阶段定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageDefinition:
    """一个流程阶段的输入/输出和执行约束。"""

    id: str
    agent_id: str
    input_kinds: tuple[str, ...] = ()
    output_kind: str = "Artifact"
    parallelizable: bool = False
    required_predecessors: tuple[str, ...] = ()
    quality_gate: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.agent_id.strip():
            raise ValueError("StageDefinition.id 和 agent_id 不能为空")
        if self.output_kind is not None and not self.output_kind.strip():
            raise ValueError("StageDefinition.output_kind 不能为空")


@dataclass(frozen=True)
class TransitionRule:
    """两个阶段之间的合法转换。"""

    from_stage: str
    to_stage: str
    condition: str = ""

    def __post_init__(self) -> None:
        if not self.from_stage.strip() or not self.to_stage.strip():
            raise ValueError("TransitionRule 阶段不能为空")


@dataclass(frozen=True)
class ProcessLimits:
    """动态扩展时的全局安全边界。"""

    max_architecture_depth: int = 3
    max_modules: int = 32
    max_implementation_units: int = 512

    def __post_init__(self) -> None:
        if self.max_architecture_depth < 1:
            raise ValueError("max_architecture_depth 必须大于 0")
        if self.max_modules < 1 or self.max_implementation_units < 1:
            raise ValueError("流程规模上限必须大于 0")


@dataclass(frozen=True)
class ProcessDefinition:
    """软件交付流程的稳定规则，不携带具体项目节点。"""

    id: str
    name: str
    stages: tuple[StageDefinition, ...]
    transitions: tuple[TransitionRule, ...] = ()
    limits: ProcessLimits = ProcessLimits()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("ProcessDefinition.id 和 name 不能为空")
        if not self.stages:
            raise ValueError("ProcessDefinition 至少需要一个阶段")
        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("ProcessDefinition 阶段 ID 不能重复")
        known = set(stage_ids)
        for transition in self.transitions:
            if transition.from_stage not in known or transition.to_stage not in known:
                raise ValueError("TransitionRule 引用了不存在的阶段")

    def stage(self, stage_id: str) -> StageDefinition | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)

    def transition_allowed(self, from_stage: str, to_stage: str) -> bool:
        return any(
            item.from_stage == from_stage and item.to_stage == to_stage
            for item in self.transitions
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "stages": [
                {
                    "id": stage.id,
                    "agent_id": stage.agent_id,
                    "input_kinds": list(stage.input_kinds),
                    "output_kind": stage.output_kind,
                    "parallelizable": stage.parallelizable,
                    "required_predecessors": list(stage.required_predecessors),
                    "quality_gate": stage.quality_gate,
                }
                for stage in self.stages
            ],
            "transitions": [
                {
                    "from_stage": transition.from_stage,
                    "to_stage": transition.to_stage,
                    "condition": transition.condition,
                }
                for transition in self.transitions
            ],
            "limits": {
                "max_architecture_depth": self.limits.max_architecture_depth,
                "max_modules": self.limits.max_modules,
                "max_implementation_units": self.limits.max_implementation_units,
            },
        }


@dataclass
class ProcessRegistry:
    """受控流程目录。"""

    _processes: dict[str, ProcessDefinition]

    def __init__(self) -> None:
        self._processes = {}

    def register(self, process: ProcessDefinition) -> None:
        if process.id in self._processes:
            raise ValueError(f"ProcessDefinition '{process.id}' 已注册")
        self._processes[process.id] = process

    def get(self, process_id: str) -> ProcessDefinition | None:
        return self._processes.get(process_id)

    def processes(self) -> tuple[ProcessDefinition, ...]:
        return tuple(self._processes.values())


software_delivery_process = ProcessDefinition(
    id="software_delivery",
    name="软件项目交付",
    stages=(
        StageDefinition("requirement", "requirement_agent", output_kind="RequirementObject"),
        StageDefinition("architecture_blueprint", "architecture_agent", ("RequirementObject",), "ArchitectureBlueprint"),
        StageDefinition("architecture_module", "architecture_agent", ("ArchitectureBlueprint",), "ModuleDesign", parallelizable=True),
        StageDefinition("architecture_implementation", "architecture_agent", ("ModuleDesign",), "ImplementationDesign", parallelizable=True),
        StageDefinition("architecture_integration", "architecture_agent", ("ArchitectureBlueprint", "ModuleDesign", "ImplementationDesign"), "ArchitectureDesignBundle", quality_gate="architecture_quality"),
        StageDefinition("contract", "architecture_contract_agent", ("ArchitectureDesignBundle",), "ImplementationContract"),
        StageDefinition("task", "task_agent", ("RequirementObject", "ImplementationContract"), "TaskObject"),
        StageDefinition("environment", "bootstrap_agent", ("ImplementationContract",), "EnvironmentReport"),
        StageDefinition("implementation", "code_agent", ("ImplementationContract", "TaskObject", "EnvironmentReport"), "ChangeSet", parallelizable=True),
        StageDefinition("integration", "code_integration_agent", ("ChangeSet",), "WorkspaceSnapshot"),
        StageDefinition("test", "test_agent", ("WorkspaceSnapshot",), "TestEvidence"),
        StageDefinition("review", "review_agent", ("RequirementObject", "WorkspaceSnapshot", "TestEvidence"), "ReviewReport"),
    ),
    transitions=(
        TransitionRule("requirement", "architecture_blueprint"),
        TransitionRule("architecture_blueprint", "architecture_module"),
        TransitionRule("architecture_module", "architecture_implementation"),
        TransitionRule("architecture_implementation", "architecture_integration"),
        TransitionRule("architecture_integration", "contract"),
        TransitionRule("contract", "task"),
        TransitionRule("task", "environment"),
        TransitionRule("environment", "implementation"),
        TransitionRule("implementation", "integration"),
        TransitionRule("integration", "test"),
        TransitionRule("test", "review"),
    ),
)


def default_process_registry() -> ProcessRegistry:
    registry = ProcessRegistry()
    registry.register(software_delivery_process)
    return registry


__all__ = [
    "ProcessDefinition",
    "ProcessLimits",
    "ProcessRegistry",
    "StageDefinition",
    "TransitionRule",
    "default_process_registry",
    "software_delivery_process",
]
