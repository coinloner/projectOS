"""ArchitectureAgent 的工具合同。"""

from app.domain.architecture.service import ArchitectureArtifactWorkflow, ArchitectureService
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

    def save_layer_contract(self, content: str) -> str:
        return self._service.save_layer_contract(content)

    def load_layer_contract(self) -> str:
        return self._service.load_layer_contract()


def register_architecture_tools(gateway: ToolGateway, project_path: str) -> None:
    """注册兼容的独占工具和新分区/集成工具。"""
    tools = ArchitectureToolSet(ArchitectureService(project_path))
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
                    ),
                    tools.save_architecture,
                ),
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
                    ),
                    workflow.create_candidate,
                ),
            ]
        ),
    )
