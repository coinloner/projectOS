"""ArchitectureAgent 的工具合同。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.domain.architecture.service import ArchitectureArtifactWorkflow, ArchitectureService
from app.domain.architecture.contract_input import ProjectContractInput
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    ImplementationDesign,
    ModuleDesign,
)
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef, ToolSetSource


class ArchitectureToolSet:
    """将 ArchitectureService 适配为架构 Agent 的本地工具。"""

    def __init__(self, service: ArchitectureService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_architecture(self, content: str) -> str:
        return self._service.save_architecture(content)

    def save_implementation_contract(self, contract: dict[str, Any]) -> str:
        return _save_contract(self._service, contract)

    def load_project_contract(self) -> str:
        return self._service.load_implementation_contract()


class ArchitectureContractToolSet:
    """ArchitectureContractAgent 的架构读取和合同写入能力。"""

    def __init__(self, service: ArchitectureService) -> None:
        self._service = service

    def load_architecture(self) -> str:
        return self._service.load_architecture()

    def load_requirement(self) -> str:
        return self._service.load_requirement()

    def save_implementation_contract(self, contract: dict[str, Any]) -> str:
        return _save_contract(self._service, contract)


def _module_design_tool_schema() -> dict[str, Any]:
    """Return the closed ModuleDesign schema with wire-only consumed aliases.

    ``BaseTool`` validates arguments before invoking the domain service, so
    model validators cannot normalize legacy ``usage/required`` fields by
    themselves.  Give only ``consumed_interfaces`` a dedicated wire schema;
    the service immediately converts it to canonical ``direction/summary``.
    """
    schema = json.loads(json.dumps(ModuleDesign.model_json_schema()))
    defs = schema.setdefault("$defs", {})
    defs["ConsumedInterfaceWire"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "interface_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "direction": {"type": "string", "enum": ["consumed"]},
            "summary": {"type": "string", "minLength": 1, "maxLength": 500},
            "usage": {"anyOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}], "default": None},
            "required": {"type": "boolean", "default": True},
        },
        "required": ["interface_id"],
    }
    consumed = schema.get("properties", {}).get("consumed_interfaces")
    if isinstance(consumed, dict):
        consumed["items"] = {"$ref": "#/$defs/ConsumedInterfaceWire"}
    return schema


def register_architecture_tools(gateway: ToolGateway, project_path: str) -> None:
    """注册兼容的独占工具和新分区/集成工具。"""
    tools = ArchitectureToolSet(ArchitectureService(project_path))
    module_design_schema = _module_design_tool_schema()
    gateway.register_toolset(
        domain="architecture",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置需求产物。可读取: requirement。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "artifact": {
                                    "type": "string",
                                    "description": "产物标识 requirement",
                                }
                            },
                            "required": ["artifact"],
                        },
                        execution_modes=("exclusive",),
                    ),
                    tools.load_artifact,
                ),
                (
                    ToolDef(
                        name="save_architecture",
                        description="保存完整架构文档到 architecture.md。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "完整 Markdown 内容"}
                            },
                            "required": ["content"],
                        },
                        execution_modes=("exclusive",),
                        completion_policy="final",
                    ),
                    tools.save_architecture,
                ),
            ]
        ),
    )

    contract_tools = ArchitectureContractToolSet(ArchitectureService(project_path))
    gateway.register_toolset(
        domain="architecture_contract",
        name="contract_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_architecture",
                        description="读取已发布的 architecture.md，供实现合同编译使用。",
                        parameters={"type": "object", "properties": {}},
                        execution_modes=("exclusive",),
                    ),
                    contract_tools.load_architecture,
                ),
                (
                    ToolDef(
                        name="load_requirement",
                        description="读取 requirement.md 中的 AC 编号，供实现合同建立可追踪映射。",
                        parameters={"type": "object", "properties": {}},
                        execution_modes=("exclusive",),
                    ),
                    contract_tools.load_requirement,
                ),
                (
                    ToolDef(
                        name="save_implementation_contract",
                        description=(
                            "保存经过控制面校验的唯一 Project Contract。contract 必须是结构化对象，"
                            "层级使用 layers 数组对象表达，不要传 JSON 字符串。"
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "contract": ProjectContractInput.model_json_schema(),
                            },
                            "required": ["contract"],
                            "additionalProperties": False,
                        },
                        execution_modes=("exclusive",),
                        completion_policy="final",
                    ),
                    contract_tools.save_implementation_contract,
                ),
            ]
        ),
    )

    layered_contract_workflow = ArchitectureArtifactWorkflow(project_path)
    gateway.register_toolset(
        domain="architecture_contract",
        name="structured_design_compiler",
        toolset=ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="compile_project_contract_from_designs",
                        description="读取已通过架构质量门的三层设计对象，确定性编译唯一 Project Contract。",
                        parameters={
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        execution_modes=("integration",),
                        completion_policy="final",
                    ),
                    layered_contract_workflow.compile_project_contract_from_designs,
                )
            ]
        ),
    )


    workflow = ArchitectureArtifactWorkflow(project_path)
    gateway.register_toolset(
        domain="architecture",
        name="artifact_workflow",
        toolset=ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="write_architecture_blueprint",
                        description="写入 depth=0 的总体架构蓝图对象；只能描述系统边界、层级和模块清单。",
                        parameters={
                            "type": "object",
                            "properties": {"design": ArchitectureBlueprint.model_json_schema()},
                            "required": ["design"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned",),
                        completion_policy="final",
                    ),
                    lambda context, design: workflow.write_staged_design(
                        context, design
                    ),
                ),
                (
                    ToolDef(
                        name="write_module_design",
                        description="写入 depth=1 的单模块架构设计对象；必须引用总体蓝图 design_id。",
                        parameters={
                            "type": "object",
                            "properties": {"design": module_design_schema},
                            "required": ["design"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned",),
                        completion_policy="final",
                    ),
                    lambda context, design: workflow.write_staged_design(
                        context, design
                    ),
                ),
                (
                    ToolDef(
                        name="write_implementation_design",
                        description="写入 depth=2 的模块实现准备对象；必须包含完整文件 ownership 和实现单元。",
                        parameters={
                            "type": "object",
                            "properties": {"design": ImplementationDesign.model_json_schema()},
                            "required": ["design"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned",),
                        completion_policy="final",
                    ),
                    lambda context, design: workflow.write_staged_design(
                        context, design
                    ),
                ),
                (
                    ToolDef(
                        name="integrate_architecture_designs",
                        description="读取当前工作项授权的分层架构对象，做确定性校验并创建架构候选。",
                        parameters={"type": "object", "properties": {}, "additionalProperties": False},
                        execution_modes=("integration",),
                        completion_policy="final",
                    ),
                    workflow.integrate_structured_designs,
                ),
                (
                    ToolDef(
                        name="load_architecture_input",
                        description="读取当前工作项已授权的冻结产物引用。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "ref_id": {"type": "string", "description": "任务中列出的产物引用"}
                            },
                            "required": ["ref_id"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned", "integration"),
                    ),
                    workflow.load_input,
                ),
                (
                    ToolDef(
                        name="write_staged_architecture",
                        description="将完整架构 Markdown 写入本工作项唯一的暂存 slot，不会发布。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned",),
                        completion_policy="final",
                    ),
                    workflow.write_staged,
                ),
                (
                    ToolDef(
                        name="create_architecture_candidate",
                        description="基于当前已授权暂存输出创建架构候选版本，不会直接发布。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "整合后的完整 Markdown 内容"}},
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        execution_modes=("integration",),
                        completion_policy="final",
                    ),
                    workflow.create_candidate,
                ),
            ]
        ),
    )


def _save_contract(service: ArchitectureService, contract: dict[str, Any]) -> str:
    """Return machine-readable success/failure while keeping validation atomic."""

    # ToolDef exposes a single named argument; the domain service receives the
    # contract object itself.  Keeping this unwrap at the adapter boundary means
    # callers cannot accidentally persist an envelope as the canonical object.
    if isinstance(contract, dict) and set(contract) == {"contract"}:
        contract = contract["contract"]
    try:
        result = service.save_implementation_contract(contract)
    except ValidationError as error:
        return json.dumps(
            {
                "ok": False,
                "error_type": "contract_schema",
                "errors": [_validation_error_item(item) for item in error.errors()],
            },
            ensure_ascii=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return json.dumps(
            {
                "ok": False,
                "error_type": "contract_validation",
                "errors": [_semantic_error_item(contract, str(error))],
            },
            ensure_ascii=False,
        )
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return json.dumps({"ok": True, "message": str(result)}, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)


def _validation_error_item(error: dict[str, Any]) -> dict[str, Any]:
    location = error.get("loc", ())
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
    item: dict[str, Any] = {
        "path": path or "$",
        "code": str(error.get("type", "invalid")),
        "message": str(error.get("msg", "输入不合法")),
    }
    if "input" in error and isinstance(error["input"], (str, int, float, bool, type(None))):
        item["value"] = error["input"]
    return item


def _semantic_error_item(contract: Any, message: str) -> dict[str, Any]:
    """Attach a useful JSON path to cross-field parser errors when possible."""

    path = "$"
    text = message.lower()
    if "owner_unit" in text or "owner_unit" in message:
        interfaces = (
            contract.get("provided_interfaces", contract.get("interfaces", []))
            if isinstance(contract, dict)
            else []
        )
        units = {
            str(item.get("unit_id"))
            for item in (contract.get("implementation_units", []) if isinstance(contract, dict) else [])
            if isinstance(item, dict)
        }
        for index, interface in enumerate(interfaces):
            if isinstance(interface, dict) and interface.get("owner_unit") not in units:
                path = f"provided_interfaces[{index}].owner_unit"
                break
    elif "重复 unit_id" in message:
        path = "implementation_units"
    elif "重复 interface_id" in message:
        path = "provided_interfaces"
    elif "循环依赖" in message:
        path = "implementation_units[].depends_on"
    return {"path": path, "code": "semantic_invalid", "message": message}
