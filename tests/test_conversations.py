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
from app.planner.service import PlannerFailure


class FakeRunService:
    def __init__(self) -> None:
        self.last_goal: str | None = None

    def start_dynamic_plan(self, *, project_path: str, goal: str, plan_id: str) -> StartedRun:
        self.last_goal = goal
        return StartedRun(
            trace_id="tr-conversation",
            plan_id=plan_id,
            workflow_id="dynamic",
            status="running",
        )

    def start_patch_plan(self, *, project_path: str, trace_id: str, change_request: str) -> StartedRun:
        return StartedRun(
            trace_id=trace_id,
            plan_id="plan-patch",
            workflow_id="dynamic",
            status="running",
        )


class FailingPlannerService(FakeRunService):
    def start_dynamic_plan(self, *, project_path: str, goal: str, plan_id: str) -> StartedRun:
        raise RuntimeError("provider HTTP 402: balance insufficient")


class TracedFailingPlannerService(FakeRunService):
    def start_dynamic_plan(self, *, project_path: str, goal: str, plan_id: str) -> StartedRun:
        from app.orchestration.retry import FailureKind, FailureSignal

        raise PlannerFailure(
            "Planner Provider 调用失败",
            trace_id="tr-planner-failure",
            signal=FailureSignal(
                kind=FailureKind.PROVIDER_EMPTY_RESPONSE,
                summary="LLM Provider 返回 None 或空响应",
            ),
        )


class ConversationTest(unittest.TestCase):
    def test_intent_classifier_prioritizes_inspection_and_modification(self) -> None:
        self.assertEqual(classify_intent("查看运行结果"), ConversationIntent.INSPECT_RESULT)
        self.assertEqual(classify_intent("继续修改架构"), ConversationIntent.MODIFY_REQUEST)
        self.assertEqual(classify_intent("恢复任务"), ConversationIntent.RESUME)
        self.assertEqual(
            classify_intent("构建带管理员库存调整接口的电商平台"),
            ConversationIntent.NEW_REQUEST,
        )

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

    def test_first_message_passes_raw_goal_without_minimal_plan_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            run_service = FakeRunService()
            service = ConversationService(run_service=run_service)

            service.send_message(
                project_path=directory,
                conversation_id=conversation.id,
                content="为 Todo 应用实现纯 Python REST API",
            )

            self.assertEqual(
                run_service.last_goal, "为 Todo 应用实现纯 Python REST API"
            )

    def test_provider_failure_is_persisted_as_conversation_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            service = ConversationService(run_service=FailingPlannerService())
            with self.assertRaises(PlannerFailure):
                service.send_message(
                    project_path=directory,
                    conversation_id=conversation.id,
                    content="设计一个 API",
                )
            turns = ConversationStore(directory).turns(conversation.id)
            self.assertIn("provider HTTP 402", turns[-1].content)

    def test_traced_planner_failure_is_linked_to_conversation_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            service = ConversationService(run_service=TracedFailingPlannerService())
            with self.assertRaises(PlannerFailure):
                service.send_message(
                    project_path=directory,
                    conversation_id=conversation.id,
                    content="设计一个 API",
                )
            turns = ConversationStore(directory).turns(conversation.id)
            self.assertEqual(turns[-1].trace_id, "tr-planner-failure")
            self.assertIn("tr-planner-failure", turns[-1].content)
            self.assertIn("provider_empty_response", turns[-1].content)

    def test_follow_up_message_gets_minimal_plan_with_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            trace = TraceStore(directory).start_trace("设计 API")
            store.append(conversation.id, role="user", content="设计 API")
            store.append(
                conversation.id,
                role="system",
                content=f"已创建执行 Trace: {trace.trace_id}",
                trace_id=trace.trace_id,
            )
            TraceStore(directory).finish_trace(trace, "completed")
            run_service = FakeRunService()
            service = ConversationService(run_service=run_service)

            service.send_message(
                project_path=directory,
                conversation_id=conversation.id,
                content="再加一个删除接口",
            )

            self.assertIn("最小可执行计划", run_service.last_goal or "")
            self.assertIn("[user] 设计 API", run_service.last_goal or "")

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

    def test_modification_uses_patch_entrypoint_after_terminal_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            conversation = store.create("demo")
            trace = TraceStore(directory).start_trace("设计 API")
            store.append(
                conversation.id,
                role="system",
                content=f"已创建执行 Trace: {trace.trace_id}",
                trace_id=trace.trace_id,
            )
            TraceStore(directory).finish_trace(trace, "completed")

            action = ConversationService(run_service=FakeRunService()).send_message(
                project_path=directory,
                conversation_id=conversation.id,
                content="修改 API 为 GraphQL",
            )

            self.assertEqual(action.intent, ConversationIntent.MODIFY_REQUEST)
            self.assertEqual(action.started_run.plan_id, "plan-patch")


if __name__ == "__main__":
    unittest.main()
