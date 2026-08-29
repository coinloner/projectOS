"""项目交付产物的 owner、阶段和依赖契约。

该对象解决一个常见的编排错误：节点把未来阶段才能生成的文件当成自己的
required_paths。交付契约在编译计划时校验 owner 唯一性和阶段顺序，避免将
错误留给 LLM 或最终 Review 才发现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DeliveryArtifact:
    path: str
    owner: str
    phase: str
    consumers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("path", "owner", "phase"):
            if not getattr(self, field).strip():
                raise ValueError(f"DeliveryArtifact.{field} 不能为空")


@dataclass(frozen=True)
class DeliveryContract:
    artifacts: tuple[DeliveryArtifact, ...]

    def __post_init__(self) -> None:
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("DeliveryContract 包含重复产物路径")
        owners: dict[str, str] = {}
        for item in self.artifacts:
            previous = owners.get(item.path)
            if previous is not None and previous != item.owner:
                raise ValueError(f"产物 {item.path} 有多个 owner: {previous}, {item.owner}")
            owners[item.path] = item.owner

    @classmethod
    def project_delivery(cls) -> "DeliveryContract":
        return cls((
            DeliveryArtifact("requirement.md", "project-documents", "planning"),
            DeliveryArtifact("architecture.md", "project-documents", "planning"),
            DeliveryArtifact("architecture_contract.md", "project-documents", "planning"),
            DeliveryArtifact("tasks.md", "project-documents", "planning"),
            DeliveryArtifact("environment.md", "environment", "environment", ("tests", "review")),
            DeliveryArtifact("implementation.md", "code-integration", "integration", ("tests", "review")),
            DeliveryArtifact("tests.md", "tests", "verification", ("review",)),
            DeliveryArtifact("review.md", "review", "review"),
        ))

    def validate_plan(self, work_items: Iterable[object]) -> None:
        items = tuple(work_items)
        by_id = {str(item.id): item for item in items}
        aliases = {
            artifact.owner: next(
                (item_id for item_id in by_id if item_id == artifact.owner or item_id.endswith("-" + artifact.owner)),
                None,
            )
            for artifact in self.artifacts
        }
        missing = sorted(owner for owner, item_id in aliases.items() if item_id is None)
        if missing:
            raise ValueError("DeliveryContract 缺少产物 owner 节点: " + ", ".join(missing))
        dependencies = {
            str(item.id): {
                next(
                    (known for known in by_id if known == dependency or known.endswith("-" + str(dependency))),
                    str(dependency),
                )
                for dependency in getattr(item, "dependency_ids", ())
            }
            for item in items
        }
        known_ids = set(by_id)

        def depends_on(consumer: str, owner: str, seen: set[str] | None = None) -> bool:
            seen = seen or set()
            if consumer in seen:
                return False
            seen.add(consumer)
            direct = dependencies.get(consumer, set())
            if owner in direct:
                return True
            return any(depends_on(parent, owner, seen) for parent in direct)

        for artifact in self.artifacts:
            owner_id = aliases[artifact.owner]
            assert owner_id is not None
            for consumer_id in artifact.consumers:
                resolved_consumer = next(
                    (item_id for item_id in known_ids if item_id == consumer_id or item_id.endswith("-" + consumer_id)),
                    None,
                )
                if resolved_consumer is None:
                    raise ValueError(f"产物 {artifact.path} 引用了不存在的消费者: {consumer_id}")
                if not depends_on(resolved_consumer, owner_id):
                    raise ValueError(
                        f"产物 {artifact.path} 的消费者 {consumer_id} 未声明对 owner {artifact.owner} 的依赖"
                    )

    def owner_for(self, path: str) -> str | None:
        normalized = path.removeprefix("workspace/").lstrip("/")
        item = next((item for item in self.artifacts if item.path == normalized), None)
        return item.owner if item else None
