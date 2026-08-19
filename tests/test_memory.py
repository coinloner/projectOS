import tempfile
import unittest

from app.memory.context import MemoryContextAssembler
from app.memory.store import MemoryStore
from app.memory.summary import validate_summary


class FakeEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, float]:
        value = text.lower()
        return (1.0, 0.0) if any(word in value for word in ("storage", "sql")) else (0.0, 1.0)


class MemoryStoreTest(unittest.TestCase):
    def test_events_are_append_only_and_persisted_per_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            first = store.append(
                trace_id="tr-demo",
                role="user",
                event_type="goal",
                content="实现一个接口",
            )
            second = store.append(
                trace_id="tr-demo",
                role="assistant",
                event_type="agent_output",
                content="已完成",
            )

            self.assertEqual((first.sequence, second.sequence), (1, 2))
            path = f"{directory}/.projectos/runs/tr-demo/memory.jsonl"
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(len(stream.read().splitlines()), 2)
            self.assertEqual(store.latest_sequence("tr-demo"), 2)

    def test_events_support_structured_filters_and_bounded_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.append(
                trace_id="tr-demo", role="system", content="one", work_item_id="wi-1"
            )
            store.append(
                trace_id="tr-demo", role="tool", content="two", work_item_id="wi-2"
            )
            store.append(
                trace_id="tr-demo", role="assistant", content="three", work_item_id="wi-1"
            )
            self.assertEqual(
                [event.content for event in store.events("tr-demo", work_item_id="wi-1")],
                ["one", "three"],
            )
            self.assertEqual(
                [event.content for event in store.events("tr-demo", roles=("tool",))],
                ["two"],
            )
            self.assertEqual(
                [event.content for event in store.events("tr-demo", after_sequence=1)],
                ["two", "three"],
            )
            view = store.view("tr-demo", limit=2, max_chars=20)
            self.assertLessEqual(len(view.as_prompt()), 500)

    def test_checkpoint_is_available_as_recovery_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.append(trace_id="tr-demo", role="user", content="goal")
            store.checkpoint(
                "tr-demo", state={"plan_id": "p1", "pending_work_items": ["wi-2"]}
            )
            view = store.view("tr-demo")
            self.assertEqual(view.checkpoint["plan_id"], "p1")
            self.assertIn("checkpoint", view.as_prompt())

    def test_content_is_clipped_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event = MemoryStore(directory).append(
                trace_id="tr-demo", role="assistant", content="x" * 20_000
            )
            self.assertLessEqual(len(event.content), 16_000)
            self.assertIn("内容已截断", event.content)

    def test_fts_search_is_rebuildable_and_excludes_prompt_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.append(
                trace_id="tr-search",
                role="system",
                event_type="agent_input",
                content="PostgreSQL should not be retrieved from the prompt input",
            )
            expected = store.append(
                trace_id="tr-search",
                role="assistant",
                event_type="agent_output",
                content="PostgreSQL migration passed in attempt two",
            )

            matches = store.search("PostgreSQL migration", trace_id="tr-search")

            self.assertEqual([event.id for event in matches], [expected.id])

    def test_durable_memory_requires_explicit_promotion_and_can_be_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            candidate = store.propose_durable(
                trace_id="tr-memory",
                content="项目偏好：所有文档使用简体中文",
                source_refs=("tr-memory",),
            )
            self.assertEqual(store.search("简体中文", trace_id="tr-memory"), ())

            promoted = store.promote(candidate.id, trace_id="tr-memory")
            self.assertEqual(store.search("简体中文", trace_id="tr-memory"), (promoted,))

            store.supersede(promoted.id, trace_id="tr-memory")
            self.assertEqual(store.search("简体中文", trace_id="tr-memory"), ())

    def test_context_assembler_keeps_recent_and_recalled_events_with_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            store.append(
                trace_id="tr-context",
                role="assistant",
                event_type="agent_output",
                content="历史上 API 使用 /todos 路径",
                tier="episodic",
            )
            store.append(
                trace_id="tr-context",
                role="assistant",
                event_type="agent_output",
                content="当前工作项正在设计接口",
                work_item_id="wi-api",
                tier="working",
            )

            context = MemoryContextAssembler(store, max_chars=900).build(
                trace_id="tr-context",
                work_item_id="wi-api",
                query="API todos",
            )

            prompt = context.as_prompt()
            self.assertLessEqual(len(prompt), 900)
            self.assertIn("当前工作项正在设计接口", prompt)

    def test_temporary_memory_is_stored_but_not_retrieved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            event = store.append(
                trace_id="tr-temporary",
                role="user",
                content="只是闲聊，不参与任务上下文",
                tier="temporary",
            )

            self.assertEqual(store.events("tr-temporary"), (event,))
            self.assertEqual(store.search("闲聊", trace_id="tr-temporary"), ())

    def test_trace_summary_is_episodic_and_replaces_previous_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            goal = store.append(
                trace_id="tr-summary",
                role="user",
                event_type="goal",
                content="完成 API 设计",
            )
            result = store.append(
                trace_id="tr-summary",
                role="control",
                event_type="work_item_result",
                content="API 设计已完成",
                work_item_id="wi-api",
            )
            first = store.summarize_trace("tr-summary", status="completed")
            second = store.summarize_trace("tr-summary", status="completed")

            self.assertEqual(first.tier, "episodic")
            self.assertIn(goal.id, first.source_refs)
            self.assertIn(result.id, first.source_refs)
            self.assertEqual(
                store.search("API 设计", trace_id="tr-summary", tiers=("episodic",)),
                (second,),
            )

    def test_optional_embedding_provider_is_combined_with_fts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory, embedding_provider=FakeEmbeddingProvider())
            expected = store.append(
                trace_id="tr-vector",
                role="assistant",
                event_type="agent_output",
                content="SQL schema is ready",
            )

            matches = store.search("storage", trace_id="tr-vector")

            self.assertEqual(matches[0].id, expected.id)

    def test_cross_trace_search_only_returns_promoted_durable_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = MemoryStore(directory)
            candidate = first.propose_durable(
                trace_id="tr-old",
                content="项目统一使用 Python 标准库",
            )
            first.promote(candidate.id, trace_id="tr-old")
            first.append(
                trace_id="tr-noisy",
                role="assistant",
                event_type="agent_output",
                content="Python 标准库只是本次临时建议",
            )

            matches = MemoryStore(directory).search_durable(
                "Python 标准库", limit=10
            )

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].trace_id, "tr-old")
            self.assertEqual(matches[0].tier, "durable")

            context = MemoryContextAssembler(MemoryStore(directory)).build(
                trace_id="tr-new",
                work_item_id=None,
                query="Python 标准库",
                include_durable=True,
            )
            self.assertIn("tier=durable", context.as_prompt())

    def test_durable_candidate_queue_excludes_promoted_and_expired_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            pending = store.propose_durable(
                trace_id="tr-review", content="待审批的偏好"
            )
            promoted_candidate = store.propose_durable(
                trace_id="tr-review", content="已经批准的偏好"
            )
            store.promote(promoted_candidate.id, trace_id="tr-review")

            candidates = store.durable_candidates()
            self.assertEqual([event.id for event in candidates], [pending.id])

            expired = store.expire(pending.id, trace_id="tr-review")
            self.assertEqual(expired.event_type, "memory_expired")
            self.assertEqual(store.durable_candidates(), ())

    def test_cleanup_expired_is_idempotent_and_preserves_original_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            source = store.append(
                trace_id="tr-clean", role="assistant", content="临时结果",
                tier="temporary", expires_at="2000-01-01T00:00:00+00:00",
            )
            first = store.cleanup_expired(trace_id="tr-clean")
            second = store.cleanup_expired(trace_id="tr-clean")
            self.assertEqual([event.event_type for event in first], ["memory_expired"])
            self.assertEqual(second, ())
            self.assertEqual(store.events("tr-clean")[0].id, source.id)

    def test_summary_quality_rejects_missing_sources_or_status(self) -> None:
        quality = validate_summary(
            content="Trace 运行摘要：status=completed",
            status="completed",
            source_refs=("mem-1",),
            available_event_ids=set(),
        )
        self.assertFalse(quality.valid)
        self.assertIn("不存在", " ".join(quality.issues))


if __name__ == "__main__":
    unittest.main()
