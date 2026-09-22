import unittest

from app.agent.result import AgentResult
from app.orchestration.node_result import NodeResult, NodeStatus


class NodeResultTest(unittest.TestCase):
    def test_completed_agent_result_becomes_completed_node_result(self) -> None:
        result = NodeResult.from_agent_result(
            work_item_id="requirement",
            agent_id="requirement_agent",
            result=AgentResult.completed("需求草稿"),
        )

        self.assertEqual(result.status, NodeStatus.COMPLETED)
        self.assertEqual(result.work_item_id, "requirement")
        self.assertIn("work_item_id", result.as_dict())
        self.assertNotIn("node_id", result.as_dict())
        self.assertEqual(result.agent_id, "requirement_agent")
        self.assertEqual(result.content, "需求草稿")

    def test_capability_request_is_preserved_at_node_boundary(self) -> None:
        result = NodeResult.from_agent_result(
            work_item_id="research",
            agent_id="requirement_agent",
            result=AgentResult.needs_capability(
                "external_research", "需要查询行业规范"
            ),
        )

        self.assertEqual(result.status, NodeStatus.NEEDS_CAPABILITY)
        self.assertEqual(result.capability_request.capability, "external_research")

    def test_failed_factory_preserves_execution_identity(self) -> None:
        result = NodeResult.failed(
            work_item_id="requirement",
            agent_id="requirement_agent",
            error="Agent unavailable",
        )

        self.assertEqual(result.status, NodeStatus.FAILED)
        self.assertEqual(result.error, "Agent unavailable")

    def test_checkpoint_migrates_legacy_node_id_to_work_item_id(self) -> None:
        result = NodeResult.from_dict(
            {
                "node_id": "legacy-node",
                "agent_id": "requirement_agent",
                "status": "completed",
                "content": "ok",
            }
        )
        self.assertEqual(result.work_item_id, "legacy-node")
        self.assertEqual(result.as_dict()["work_item_id"], "legacy-node")
