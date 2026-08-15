import unittest

from app.agent.result import AgentStatus, from_llm_content


class AgentResultTest(unittest.TestCase):
    def test_plain_text_is_a_completed_result(self) -> None:
        result = from_llm_content("需求文档已完成")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.content, "需求文档已完成")
        self.assertIsNone(result.capability_request)

    def test_strict_capability_request_json_is_structured(self) -> None:
        result = from_llm_content(
            '{"type": "capability_request", '
            '"capability": "external_research", '
            '"reason": "需要查询最新行业规范"}'
        )

        self.assertEqual(result.status, AgentStatus.NEEDS_CAPABILITY)
        self.assertEqual(result.capability_request.capability, "external_research")
        self.assertEqual(result.capability_request.reason, "需要查询最新行业规范")

    def test_non_request_json_remains_completed_content(self) -> None:
        content = '{"type": "summary", "content": "done"}'

        result = from_llm_content(content)

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.content, content)


if __name__ == "__main__":
    unittest.main()
