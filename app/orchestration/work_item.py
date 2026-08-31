"""编排执行层的 WorkItem 与依赖模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import fnmatch
import hashlib
import json

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
    # Stable identity of the execution contract.  This is populated when a
    # plan is compiled and carried across retries/restores; failure diagnostics
    # are deliberately excluded so a repair can attach new evidence without
    # changing the authorization envelope.
    output_kind: str | None = None
    contract_digest: str | None = None

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
        # A retry/repair overlay must never turn a scoped grant into a global
        # deny (for example forbidden_paths=["**"]).  Reject broad deny
        # patterns and any concrete ownership collision before a Worker is
        # started; otherwise the failure only appears after the Agent has
        # generated content and attempted to persist it.
        if self.allowed_paths and any(path in {"*", "**"} for path in self.forbidden_paths):
            raise ValueError("WorkItem.forbidden_paths 不能使用全局通配符")
        for owned in self.owned_files:
            normalized = owned.replace("\\", "/").lstrip("/")
            if any(
                fnmatch.fnmatch(normalized, pattern.replace("**", "*"))
                for pattern in self.forbidden_paths
            ):
                raise ValueError(
                    f"WorkItem.owned_files 命中 forbidden_paths: {owned}"
                )
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
        if self.output_kind is None:
            object.__setattr__(self, "output_kind", _default_output_kind(self.execution_mode))
        elif not self.output_kind.strip():
            raise ValueError("WorkItem.output_kind 不能是空字符串")
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
        computed_digest = self._compute_contract_digest()
        if self.contract_digest is None:
            object.__setattr__(self, "contract_digest", computed_digest)
        elif self.contract_digest != computed_digest:
            raise ValueError("WorkItem.contract_digest 与执行合同不匹配")

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        return tuple(dependency.work_item_id for dependency in self.dependencies)

    @property
    def slot(self) -> str | None:
        """Canonical partition slot; serialized legacy plans may still use output_slot."""
        return self.output_slot

    def _compute_contract_digest(self) -> str:
        """Return the digest of fields that define execution authorization.

        Objective/failure evidence are mutable diagnostics and are not part of
        this identity.  Controlled repair that narrows paths or adds denies
        must explicitly clear ``contract_digest`` so the new contract is
        re-sealed after deterministic boundary checks.
        """
        payload = {
            "agent_id": self.agent_id,
            "execution_mode": self.execution_mode.value,
            "input_refs": [ref.ref_id for ref in self.input_refs],
            "dependencies": [
                {
                    "work_item_id": dep.work_item_id,
                    "source": dep.source.value,
                    "rule_id": dep.rule_id,
                }
                for dep in self.dependencies
            ],
            "output_key": self.output_key,
            "artifact_key": self.artifact_key,
            "output_slot": self.output_slot,
            "publish_target": self.publish_target,
            "candidate_from_work_item_id": self.candidate_from_work_item_id,
            "implementation_unit_id": self.implementation_unit_id,
            "required_paths": list(self.required_paths),
            "owned_files": list(self.owned_files),
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "policy_id": self.policy_id,
            "policy_refs": list(self.policy_refs),
            "skill_refs": list(self.skill_refs),
            "requirement_ids": list(self.requirement_ids),
            "delivery_contract": self.delivery_contract,
            "output_kind": self.output_kind,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def contract(self) -> dict[str, object]:
        """Read-only contract projection used by retry and audit validators."""
        return {
            "contract_digest": self.contract_digest,
            "agent_id": self.agent_id,
            "execution_mode": self.execution_mode.value,
            "output_kind": self.output_kind,
            "input_refs": [ref.ref_id for ref in self.input_refs],
            "dependencies": list(self.dependency_ids),
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "required_paths": list(self.required_paths),
            "owned_files": list(self.owned_files),
        }

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


def _default_output_kind(execution_mode: ExecutionMode) -> str:
    return {
        ExecutionMode.PARTITIONED: "partition_artifact",
        ExecutionMode.INTEGRATION: "integrated_artifact",
        ExecutionMode.QUALITY_GATE: "quality_report",
        ExecutionMode.EXCLUSIVE: "exclusive_artifact",
    }[execution_mode]
