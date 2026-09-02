import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.agent.base_agent import BaseAgent
from app.agent.result import AgentStatus
from app.execution_context import ExecutionContext, ExecutionMode
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource
from app.tool_manager.source import ToolExecutionError


class FakeCrewAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.tasks: list[object] = []

    def execute_task(self, task: object) -> str:
        self.tasks.append(task)
        return '{"type": "capability_request", "capability": "external_research", "reason": "需要规范"}'


class CrewAIAgentAdapterTest(unittest.TestCase):
    def test_empty_provider_return_is_not_completed(self) -> None:
        class EmptyAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return ""

        with patch("app.agent.base_agent.Agent", side_effect=EmptyAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=ToolGateway(),
                domain="requirement",
                role="需求分析师",
                goal="生成需求文档",
                backstory="整理用户需求",
            )
            with self.assertRaisesRegex(RuntimeError, "terminal signal"):
                agent.run("生成一个项目需求")

    def test_implementation_response_protocol_text_is_not_artifact_budget(self) -> None:
        class LargeAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return "x" * 6000

        with patch("app.agent.base_agent.Agent", side_effect=LargeAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=ToolGateway(), domain="architecture", role="架构师",
                goal="设计", backstory="分层",
            )
            context = ExecutionContext(
                trace_id="tr-budget", work_item_id="wi-budget", agent_id="architecture_agent",
                execution_mode=ExecutionMode.PARTITIONED, slot="implementation-api",
            )
            result = agent.run("生成实现设计", context=context)
            self.assertEqual(result.status, AgentStatus.COMPLETED)

    def test_base_agent_delegates_tool_runtime_to_crewai(self) -> None:
        gateway = ToolGateway()
        created: list[FakeCrewAgent] = []

        def build_agent(**kwargs):
            agent = FakeCrewAgent(**kwargs)
            created.append(agent)
            return agent

        with patch("app.agent.base_agent.Agent", side_effect=build_agent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=gateway,
                domain="requirement",
                role="需求分析师",
                goal="生成需求文档",
                backstory="整理用户需求",
            )
            result = agent.run("生成一个项目需求")

        self.assertEqual(result.status, AgentStatus.NEEDS_CAPABILITY)
        self.assertEqual(created[0].kwargs["tools"], [])
        self.assertEqual(created[0].kwargs["max_iter"], 10)
        self.assertEqual(created[0].tasks[0]["description"], "生成一个项目需求")

    def test_replays_textual_local_tool_call_from_compatible_gateway(self) -> None:
        """兼容未保留原生 tool-call 消息的网关，仍能真实写入本地文件。"""
        with tempfile.TemporaryDirectory() as project_path:
            writes: list[tuple[str, str]] = []
            gateway = ToolGateway()

            def write_workspace_file(path: str, content: str) -> str:
                target = Path(project_path) / "workspace" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                writes.append((path, content))
                return f"已写入 workspace/{path}"

            gateway.register_toolset(
                "code",
                "workspace",
                ToolSetSource(
                    [
                        (
                            ToolDef(
                                name="write_workspace_file",
                                description="写入 workspace 文件",
                                parameters={
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "content": {"type": "string"},
                                    },
                                    "required": ["path", "content"],
                                },
                            ),
                            write_workspace_file,
                        )
                    ]
                ),
            )

            class TextualToolCallAgent(FakeCrewAgent):
                def execute_task(self, task: object) -> str:
                    return (
                        'to=functions.write_workspace_file code:\n'
                        '{"path":"backend/app/main.py","content":"app = 1\\n"}\n'
                        "已完成"
                    )

            with patch("app.agent.base_agent.Agent", side_effect=TextualToolCallAgent), patch(
                "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
            ), patch("app.agent.base_agent.build_llm", return_value=object()):
                agent = BaseAgent(
                    gateway=gateway,
                    domain="code",
                    role="软件工程师",
                    goal="修复代码",
                    backstory="按合同落盘",
                )
                context = ExecutionContext(
                    trace_id="tr-textual",
                    work_item_id="wi-code",
                    agent_id="code_agent",
                    execution_mode=ExecutionMode.EXCLUSIVE,
                )
                result = agent.run("修复入口", context=context)

            self.assertEqual(result.status, AgentStatus.COMPLETED)
            self.assertEqual(writes, [("backend/app/main.py", "app = 1\n")])
            self.assertEqual(
                Path(project_path, "workspace/backend/app/main.py").read_text(
                    encoding="utf-8"
                ),
                "app = 1\n",
            )

    def test_local_tool_capability_claim_is_typed_protocol_failure(self) -> None:
        gateway = ToolGateway()
        gateway.register_toolset(
            "requirement",
            "local",
            ToolSetSource(
                [
                    (
                        ToolDef(
                            name="save_requirement",
                            description="save",
                            parameters={"type": "object", "properties": {}},
                        ),
                        lambda: "saved",
                    )
                ]
            ),
        )

        class MisreportingAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return '{"type":"capability_request","capability":"save_requirement","reason":"工具不可用"}'

        with patch("app.agent.base_agent.Agent", side_effect=MisreportingAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=gateway,
                domain="requirement",
                role="需求分析师",
                goal="生成需求文档",
                backstory="整理用户需求",
            )
            with self.assertRaisesRegex(ToolExecutionError, "save_requirement"):
                agent.run("生成需求")

    def test_scoped_local_tool_claim_returns_agent_result_for_runner_recovery(self) -> None:
        gateway = ToolGateway()
        gateway.register_toolset(
            "architecture",
            "local",
            ToolSetSource(
                [
                    (
                        ToolDef(
                            name="write_module_design",
                            description="save",
                            parameters={"type": "object", "properties": {}},
                        ),
                        lambda: "saved",
                    )
                ]
            ),
        )

        class MisreportingAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return '{"type":"capability_request","capability":"write_module_design","reason":"工具不可用"}'

        with patch("app.agent.base_agent.Agent", side_effect=MisreportingAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=gateway,
                domain="architecture",
                role="架构师",
                goal="设计模块",
                backstory="按结构化合同输出",
            )
            result = agent.run(
                "生成模块设计",
                context=ExecutionContext(
                    trace_id="tr-architecture",
                    work_item_id="wi-module",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.PARTITIONED,
                    slot="module-api",
                ),
            )
        self.assertEqual(result.status, AgentStatus.NEEDS_CAPABILITY)
        self.assertEqual(result.capability_request.capability, "write_module_design")

    def test_replays_bare_module_design_json_through_writer(self) -> None:
        """Relays that drop function-call envelopes still persist a valid design."""
        calls: list[dict[str, object]] = []
        gateway = ToolGateway()
        gateway.register_toolset(
            "architecture",
            "local",
            ToolSetSource(
                [
                    (
                        ToolDef(
                            name="write_module_design",
                            description="写入模块设计",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "design": {
                                        "type": "object",
                                        "properties": {"depth": {"type": "integer"}},
                                        "required": ["depth"],
                                        "additionalProperties": True,
                                    }
                                },
                                "required": ["design"],
                                "additionalProperties": False,
                            },
                        ),
                        lambda design: calls.append(design) or "saved",
                    )
                ]
            ),
        )

        class BareDesignAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return '{"schema_version":1,"depth":1,"design_id":"d1"}'

        with patch("app.agent.base_agent.Agent", side_effect=BareDesignAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            result = BaseAgent(
                gateway=gateway,
                domain="architecture",
                role="架构师",
                goal="设计模块",
                backstory="按结构化合同落盘",
            ).run(
                "生成模块设计",
                context=ExecutionContext(
                    trace_id="tr-bare-design",
                    work_item_id="wi-module",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.PARTITIONED,
                    slot="module-api",
                ),
            )

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(calls, [{"depth": 1, "schema_version": 1, "design_id": "d1"}])

    def test_does_not_replay_wrong_depth_or_plain_text(self) -> None:
        calls: list[dict[str, object]] = []
        gateway = ToolGateway()
        gateway.register_toolset(
            "architecture",
            "local",
            ToolSetSource(
                [
                    (
                        ToolDef(
                            name="write_module_design",
                            description="写入模块设计",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "design": {
                                        "type": "object",
                                        "properties": {"depth": {"type": "integer"}},
                                        "required": ["depth"],
                                        "additionalProperties": True,
                                    }
                                },
                                "required": ["design"],
                            },
                        ),
                        lambda design: calls.append(design) or "saved",
                    )
                ]
            ),
        )

        class WrongDepthAgent(FakeCrewAgent):
            def execute_task(self, task: object) -> str:
                return '{"depth":2,"design_id":"wrong"}'

        with patch("app.agent.base_agent.Agent", side_effect=WrongDepthAgent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            result = BaseAgent(
                gateway=gateway,
                domain="architecture",
                role="架构师",
                goal="设计模块",
                backstory="按结构化合同落盘",
            ).run(
                "生成模块设计",
                context=ExecutionContext(
                    trace_id="tr-wrong-depth",
                    work_item_id="wi-module",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.PARTITIONED,
                    slot="module-api",
                ),
            )

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
