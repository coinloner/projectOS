import tempfile
import unittest

from app.application.conversations import (
    ConversationBusy,
    ConversationService,
    ConversationStore,
)
from app.application.conversation_intent import ConversationIntent, classify_intent
from app.application.runs import StartedRun
from app.orchestration.trace import TraceStore


class FakeRunService:
    def start_dynamic_plan(self, *, project_path: str, goal: str, plan_id: str) -> StartedRun:
        return StartedRun(
            trace_id="tr-conversation",
            plan_id=plan_id,
            workflow_id="dynamic",
            status="running",
        )


class ConversationTest(unittest.TestCase):
    def test_intent_classifier_prioritizes_inspection_and_modification(self) -> None:
        self.assertEqual(classify_intent("查看运行结果"), ConversationIntent.INSPECT_RESULT)
        self.assertEqual(classify_intent("继续修改架构"), ConversationIntent.MODIFY_REQUEST)
        self.assertEqual(classify_intent("恢复任务"), ConversationIntent.RESUME)

    def test_conversation_and_messages_survive_new_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ConversationStore(directory)
            conversation = first.create("demo")
            first.append(conversation.id, role="user", content="先整理需求")
            first.append(
                conversation.id,
                role="system",
                content="已创建执行 Trace: tr-1",
                trace_id="tr-1",
            )

            second = ConversationStore(directory)
            loaded = second.load(conversation.id)
            turns = second.turns(conversation.id)

            self.assertEqual(loaded.project_id, "demo")
            self.assertEqual([turn.role for turn in turns], ["user", "system"])
            self.assertEqual(turns[1].trace_id, "tr-1")
            self.assertIn("[user] 先整理需求", second.prompt_history(conversation.id))

    def test_service_turn_creates_dynamic_trace_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            service = ConversationService(run_service=FakeRunService())

            user_turn, started = service.send_message(
                project_path=directory,
                conversation_id=conversation.id,
                content="设计一个简单 API",
            )

            self.assertEqual(user_turn.role, "user")
            self.assertEqual(started.trace_id, "tr-conversation")
            turns = ConversationStore(directory).turns(conversation.id)
            self.assertEqual([turn.role for turn in turns], ["user", "system"])
            self.assertEqual(turns[-1].trace_id, "tr-conversation")

    def test_terminal_trace_is_projected_as_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            trace = TraceStore(directory).start_trace("完成任务")
            store.append(
                conversation.id,
                role="system",
                content=f"已创建执行 Trace: {trace.trace_id}",
                trace_id=trace.trace_id,
            )
            TraceStore(directory).finish_trace(trace, "completed")

            turns = ConversationService(run_service=FakeRunService()).turns(
                directory, conversation.id
            )

            self.assertEqual(turns[-1].role, "assistant")
            self.assertIn("completed", turns[-1].content)

    def test_new_message_is_rejected_while_previous_trace_is_planned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            trace = TraceStore(directory).start_trace("仍在执行")
            store.append(
                conversation.id,
                role="system",
                content=f"已创建执行 Trace: {trace.trace_id}",
                trace_id=trace.trace_id,
            )

            with self.assertRaises(ConversationBusy):
                ConversationService(run_service=FakeRunService()).send_message(
                    project_path=directory,
                    conversation_id=conversation.id,
                    content="继续修改",
                )

    def test_inspection_does_not_start_a_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            trace = TraceStore(directory).start_trace("完成任务")
            store.append(
                conversation.id,
                role="system",
                content=f"已创建执行 Trace: {trace.trace_id}",
                trace_id=trace.trace_id,
            )
            TraceStore(directory).finish_trace(trace, "failed", error="测试失败")
            service = ConversationService(run_service=FakeRunService())

            action = service.send_message(
                project_path=directory,
                conversation_id=conversation.id,
                content="查看运行结果",
            )

            self.assertEqual(action.intent, ConversationIntent.INSPECT_RESULT)
            self.assertIsNone(action.started_run)
            self.assertIn("测试失败", action.message.content)


if __name__ == "__main__":
    unittest.main()
