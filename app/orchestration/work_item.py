"""编排执行层的 WorkItem 与依赖模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.retry import FailurePackage


class DependencySource(str, Enum):
    SYSTEM = "system"
    TEMPLATE = "template"
    PLANNER = "planner"


@dataclass(frozen=True)
class WorkItemDependency:
    """一个 WorkItem 依赖另一个 WorkItem 的原因。"""

    work_item_id: str
    source: DependencySource
    rule_id: str | None = None

    def __post_init__(self) -> None:
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValueError("WorkItemDependency.work_item_id 不能为空")
        if self.rule_id is not None and not self.rule_id.strip():
            raise ValueError("WorkItemDependency.rule_id 不能是空字符串")


@dataclass(frozen=True)
class WorkItem:
    """一次 Trace 中可由一个 Agent 执行的最小工作单元。"""

    id: str
    agent_id: str
    objective: str
    output_key: str
    artifact_key: str | None = None
    failure_package: FailurePackage | None = None
    dependencies: tuple[WorkItemDependency, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    policy_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.EXCLUSIVE
    input_refs: tuple[ArtifactRef, ...] = ()
    output_slot: str | None = None
    publish_target: str | None = None
    candidate_from_work_item_id: str | None = None
    implementation_unit_id: str | None = None
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    wave: int = 0
    owned_files: tuple[str, ...] = ()
    delivery_contract: dict[str, object] | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "agent_id", "objective", "output_key"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"WorkItem.{field_name} 不能为空")
        if self.artifact_key is None:
            object.__setattr__(self, "artifact_key", self.output_key)
        elif not self.artifact_key.strip():
            raise ValueError("WorkItem.artifact_key 不能为空")
        if self.policy_id is not None and not self.policy_id.strip():
            raise ValueError("WorkItem.policy_id 不能是空字符串")
        for field_name in ("implementation_unit_id",):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"WorkItem.{field_name} 不能是空字符串")
        for field_name in ("allowed_paths", "forbidden_paths", "required_paths", "policy_refs", "skill_refs", "requirement_ids"):
            if any(not value.strip() for value in getattr(self, field_name)):
                raise ValueError(f"WorkItem.{field_name} 不能包含空字符串")
        if self.wave < 0:
            raise ValueError("WorkItem.wave 不能小于 0")
        if (
            self.agent_id == "code_agent"
            and self.execution_mode is ExecutionMode.PARTITIONED
            and self.implementation_unit_id is not None
            and self.implementation_unit_id != "project-documents"
        ):
            if len(self.owned_files) != 1:
                raise ValueError(
                    "CodeAgent 实现 WorkItem 必须且只能拥有一个具体 owned_files 文件"
                )
            owned = self.owned_files[0].replace("\\", "/").strip()
            if (
                not owned
                or owned.endswith("/")
                or any(token in owned for token in ("*", "?", "[", "]"))
            ):
                raise ValueError(
                    "CodeAgent owned_files 必须是具体文件路径；目录/glob 只能用于 allowed_paths"
                )
        if self.delivery_contract is not None and not isinstance(self.delivery_contract, dict):
            raise ValueError("WorkItem.delivery_contract 必须是对象")
        dependency_ids = self.dependency_ids
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError(f"WorkItem '{self.id}' 包含重复依赖")
        if self.id in dependency_ids:
            raise ValueError(f"WorkItem '{self.id}' 不能依赖自身")
        if any(not criterion.strip() for criterion in self.acceptance_criteria):
            raise ValueError("WorkItem.acceptance_criteria 不能包含空字符串")
        if any(not constraint.strip() for constraint in self.constraints):
            raise ValueError("WorkItem.constraints 不能包含空字符串")
        if any(not non_goal.strip() for non_goal in self.non_goals):
            raise ValueError("WorkItem.non_goals 不能包含空字符串")
        self._validate_execution_grant()

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        return tuple(dependency.work_item_id for dependency in self.dependencies)

    def _validate_execution_grant(self) -> None:
        if self.execution_mode is ExecutionMode.PARTITIONED:
            if not self.output_slot or not self.output_slot.strip():
                raise ValueError("PARTITIONED WorkItem 必须指定 output_slot")
            if self.publish_target is not None or self.candidate_from_work_item_id is not None:
                raise ValueError("PARTITIONED WorkItem 不能携带发布授权")
            return
        if self.execution_mode is ExecutionMode.INTEGRATION:
            if not self.publish_target or not self.publish_target.strip():
                raise ValueError("INTEGRATION WorkItem 必须指定 publish_target")
            if self.output_slot is not None or self.candidate_from_work_item_id is not None:
                raise ValueError("INTEGRATION WorkItem 不能携带暂存或质量门授权")
            return
        if self.execution_mode is ExecutionMode.QUALITY_GATE:
            if not self.publish_target or not self.publish_target.strip():
                raise ValueError("QUALITY_GATE WorkItem 必须指定 publish_target")
            if not self.candidate_from_work_item_id or not self.candidate_from_work_item_id.strip():
                raise ValueError("QUALITY_GATE WorkItem 必须指定候选来源工作项")
            if self.output_slot is not None:
                raise ValueError("QUALITY_GATE WorkItem 不能携带暂存 slot")
            return
        if any(value is not None for value in (self.output_slot, self.publish_target, self.candidate_from_work_item_id)):
            raise ValueError("EXCLUSIVE WorkItem 不能携带分区、集成或发布授权")
