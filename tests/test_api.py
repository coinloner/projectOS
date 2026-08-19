import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.memory.store import MemoryStore
from app.orchestration.trace import TraceStore


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(projects_root=self._directory.name))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._directory.cleanup()

    def test_project_creation_and_controlled_workflow_discovery(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "demo"})
        workflows = self.client.get("/api/v1/projects/demo/workflows")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(workflows.status_code, 200)
        self.assertEqual(
            workflows.json()["workflows"],
            [
                {
                    "id": "architecture_compact",
                    "name": "精简架构设计",
                    "description": "为范围明确的小需求生成短架构候选，避免不必要的并行分区。",
                },
                {
                    "id": "architecture_parallel",
                    "name": "并行架构设计",
                    "description": "先冻结基线，再并行设计架构分区，最后整合并通过质量门发布。",
                },
                {
                    "id": "project_delivery_minimal",
                    "name": "最小项目交付",
                    "description": "使用已有需求和架构，生成任务、环境、并行代码、测试和审查结果。",
                },
            ],
        )

    def test_api_rejects_path_like_project_id_and_unknown_workflow(self) -> None:
        invalid = self.client.post("/api/v1/projects", json={"name": "../escape"})
        self.assertEqual(invalid.status_code, 422)

        self.client.post("/api/v1/projects", json={"name": "demo"})
        unknown = self.client.post(
            "/api/v1/projects/demo/runs",
            json={"goal": "设计架构", "workflow_id": "unknown"},
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertIn("未注册 Workflow", unknown.json()["detail"])

        missing_requirement = self.client.post(
            "/api/v1/projects/demo/runs",
            json={"goal": "设计架构", "workflow_id": "architecture_parallel"},
        )
        self.assertEqual(missing_requirement.status_code, 422)
        self.assertIn("缺少已发布前置产物", missing_requirement.json()["detail"])

    def test_unknown_trace_is_not_exposed_as_a_file_path(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})

        response = self.client.get("/api/v1/projects/demo/runs/../../project.yaml")

        self.assertEqual(response.status_code, 404)

    def test_project_runtime_requires_a_trusted_application_declaration(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})

        response = self.client.post("/api/v1/projects/demo/runtime/runs")

        self.assertEqual(response.status_code, 422)
        self.assertIn("没有声明可运行的 application", response.json()["detail"])

    def test_memory_is_scoped_to_a_trace_and_read_only_over_http(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        trace = TraceStore(str(project_path)).start_trace("测试记忆")
        MemoryStore(str(project_path)).append(
            trace_id=trace.trace_id,
            role="assistant",
            event_type="agent_output",
            content="已完成",
            work_item_id="wi-1",
        )

        response = self.client.get(
            f"/api/v1/projects/demo/runs/{trace.trace_id}/memory",
            params={"work_item_id": "wi-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest_sequence"], 1)
        self.assertEqual(response.json()["messages"][0]["content"], "已完成")
        searched = self.client.get(
            f"/api/v1/projects/demo/runs/{trace.trace_id}/memory",
            params={"query": "完成"},
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["messages"][0]["content"], "已完成")
        self.assertEqual(
            self.client.get("/api/v1/projects/demo/memory").status_code, 404
        )

    def test_memory_candidate_governance_requires_explicit_control_action(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        trace = TraceStore(str(project_path)).start_trace("审批记忆")
        candidate = MemoryStore(str(project_path)).propose_durable(
            trace_id=trace.trace_id, content="项目统一使用简体中文"
        )

        listed = self.client.get("/api/v1/projects/demo/memory/candidates")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["candidates"][0]["id"], candidate.id)

        promoted = self.client.post(
            f"/api/v1/projects/demo/memory/candidates/{candidate.id}/promote",
            json={"trace_id": trace.trace_id},
        )
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["event"]["lifecycle"], "active")
        self.assertEqual(
            self.client.get("/api/v1/projects/demo/memory/candidates").json()[
                "candidates"
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
