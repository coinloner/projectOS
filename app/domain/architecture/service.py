"""架构领域的本地产物能力。"""

import json
from typing import Any

from app.artifact.toolset import ArtifactToolSet
from app.artifact.store import ArtifactStore
from app.artifact.repository import ArtifactRepository
from app.execution_context import ExecutionContext, ExecutionMode
from app.domain.architecture.implementation_contract import ImplementationContractStore
from app.domain.architecture.contract_input import ProjectContractInput


class ArchitectureService:
    """封装架构节点固定的产物读写边界。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._implementation_contract = ImplementationContractStore(project_path)
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="architecture",
            readable_artifacts=("requirement",),
        )

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def load_architecture(self) -> str:
        """读取已发布架构，供合同节点使用。"""
        return ArtifactStore(self._project_path).load("architecture")

    def load_requirement(self) -> str:
        """读取验收编号，供实现合同建立需求到实现单元的映射。"""
        return ArtifactStore(self._project_path).load("requirement")

    def save_architecture(self, content: str) -> str:
        # architecture.md is a human-readable view only.  The canonical
        # machine contract is created by architecture_contract_agent after
        # this document is published, so this method must not create a second
        # layer-contract source or parse embedded JSON blocks.
        return self._artifacts.save(content)

    def save_implementation_contract(
        self, contract_input: ProjectContractInput | dict[str, Any] | str
    ) -> str:
        """Validate and atomically persist the single canonical Project Contract.

        New callers pass a structured ``ProjectContractInput`` (or its dict
        representation).  A JSON string is accepted only for direct storage
        migration and old checkpoints; the runtime tool schema never exposes
        that opaque form.
        """
        if isinstance(contract_input, ProjectContractInput):
            raw: Any = contract_input.to_canonical_dict()
        else:
            raw = json.loads(contract_input) if isinstance(contract_input, str) else contract_input
        if not isinstance(raw, dict):
            raise ValueError("Project Contract 必须是 JSON 对象")
        # The public wire DTO represents layer mappings on each layer object.
        # Convert that shape before any legacy normalization so dependency and
        # path rules are not silently replaced by defaults.
        layers = raw.get("layers")
        structured_layers = (
            isinstance(layers, list)
            and bool(layers)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and any(key in item for key in ("allowed_dependencies", "forbidden_imports", "path_mapping"))
                for item in layers
            )
        )
        if structured_layers:
            normalized = ProjectContractInput.model_validate(raw)
            raw = normalized.to_canonical_dict()
        else:
            # Historical payloads may use a layer catalogue object or a list of
            # layer descriptors.  Preserve their stable identifiers while
            # applying the legacy default policy projection.
            if isinstance(layers, dict):
                raw["layers"] = list(layers.keys())
            elif isinstance(layers, list) and any(isinstance(item, dict) for item in layers):
                raw["layers"] = [
                    item.get("name", item.get("id", item.get("layer")))
                    if isinstance(item, dict) else item
                    for item in layers
                ]
            defaults = _default_layer_contract()
            for key, value in defaults.items():
                raw.setdefault(key, value)
        contract = self._implementation_contract.save(raw)
        return json.dumps(
            {
                "ok": True,
                "contract_id": "project-contract",
                "digest": _contract_digest(contract.as_dict()),
                "unit_count": len(contract.units),
                "interface_count": len(contract.interfaces),
                "layer_count": len(contract.layers),
                "path": ImplementationContractStore.relative_path,
            },
            ensure_ascii=False,
        )

    def load_implementation_contract(self) -> str:
        return json.dumps(self._implementation_contract.load().as_dict(), ensure_ascii=False, indent=2)


class ArchitectureArtifactWorkflow:
    """架构分区、集成和发布流程的受信工具实现。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._repository = ArtifactRepository(project_path)

    def load_input(self, context: ExecutionContext, ref_id: str) -> str:
        ref = next((candidate for candidate in context.input_refs if candidate.ref_id == ref_id), None)
        if ref is None:
            raise PermissionError("当前工作项无权读取该产物引用")
        return self._repository.load_ref(ref)

    def write_staged(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有分区执行节点可以写入暂存产物")
        self._validate_size(content, _STAGED_CHAR_LIMITS.get(context.output_slot or "", 4500))
        staged = self._repository.write_staged(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="architecture",
            slot=context.output_slot or "",
            content=content,
        )
        return f"已写入架构暂存输出: {staged.ref.ref_id}"

    def create_candidate(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("只有集成节点可以创建候选版本")
        if context.publish_target != "architecture":
            raise PermissionError("当前集成节点未获 architecture 候选创建授权")
        self._validate_size(content, 9000)
        candidate = self._repository.create_candidate(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="architecture",
            content=content,
            source_refs=context.input_refs,
        )
        return (
            f"已创建架构候选: {candidate.id}；"
            f"集成状态: {candidate.report.status}"
        )

    @staticmethod
    def _validate_size(content: str, limit: int) -> None:
        if len(content) > limit:
            raise ValueError(
                f"当前架构产物超过 {limit} 字符上限；请压缩为决策、接口和未决项。"
            )


_STAGED_CHAR_LIMITS = {
    "baseline": 3200,
    "api": 4200,
    "data": 4200,
    "frontend": 4200,
    "design": 6000,
}


def _default_layer_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "layers": ["api", "application", "domain", "infrastructure"],
        "allowed_dependencies": {
            "api": ["application"],
            "application": ["domain", "infrastructure"],
            "domain": [],
            "infrastructure": ["domain", "application"],
        },
        "forbidden_imports": {
            "api": ["sqlalchemy.orm.Session"],
            "application": ["fastapi"],
            "domain": ["fastapi", "sqlalchemy", "requests"],
            "infrastructure": [],
        },
        "required_test_types": ["domain_unit", "application_unit", "api_http"],
        "path_mapping": {
            "api": ["backend/app/api/**", "backend/app/routers/**", "backend/app/routes/**"],
            "application": ["backend/app/application/**", "backend/app/services/**"],
            "domain": ["backend/app/domain/**", "backend/app/models.py", "backend/app/schemas.py"],
            "infrastructure": ["backend/app/infrastructure/**", "backend/app/repositories/**", "backend/app/database.py"],
        },
    }


def _contract_digest(payload: dict[str, Any]) -> str:
    """Return a stable audit digest without introducing a second store."""
    import hashlib

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
