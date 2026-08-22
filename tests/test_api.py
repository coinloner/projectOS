import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.memory.store import MemoryStore
from app.orchestration.trace import TraceStore
from app.project.project import Project


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
        self.assertTrue((Path(created.json()["path"]) / "start.sh").is_file())
        self.assertTrue((Path(created.json()["path"]) / "start.ps1").is_file())
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
                    "id": "project_delivery",
                    "name": "项目交付草案",
                    "description": "生成需求、架构、实施任务、首版代码、测试证据和审查报告。",
                },
                {
                    "id": "project_delivery_minimal",
                    "name": "最小项目交付",
                    "description": "使用已有需求和架构，生成任务、环境、并行代码、测试和审查结果。",
                },
            ],
        )

    def test_project_creation_accepts_custom_local_path_and_reuses_mapping(self) -> None:
        custom_path = Path(self._directory.name) / "external" / "team-demo"
        created = self.client.post(
            "/api/v1/projects", json={"name": "custom_demo", "path": str(custom_path)}
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["path"], str(custom_path.resolve()))
        self.assertTrue((custom_path / "project.yaml").is_file())
        self.assertTrue((custom_path / "start.sh").is_file())
        self.assertTrue((custom_path / "start.ps1").is_file())
        workflows = self.client.get("/api/v1/projects/custom_demo/workflows")
        self.assertEqual(workflows.status_code, 200)

        mapping = Path(self._directory.name) / ".projectos" / "project-paths.json"
        self.assertIn("custom_demo", mapping.read_text(encoding="utf-8"))

    def test_project_creation_rejects_projects_root_as_custom_path(self) -> None:
        response = self.client.post(
            "/api/v1/projects", json={"name": "bad", "path": self._directory.name}
        )
        self.assertEqual(response.status_code, 422)

    def test_import_registers_existing_project_at_custom_path(self) -> None:
        project_path = Path(self._directory.name) / "generated"
        Project.create_at(str(project_path), name="generated")

        imported = self.client.post(
            "/api/v1/projects/import",
            json={"name": "external", "path": str(project_path)},
        )
        resolved = self.client.get("/api/v1/projects/external/workflows")

        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.json()["status"], "imported")
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["project_id"], "external")

    def test_import_rejects_non_project_directory(self) -> None:
        directory = Path(self._directory.name) / "not-a-project"
        directory.mkdir()

        response = self.client.post(
            "/api/v1/projects/import",
            json={"name": "external", "path": str(directory)},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("不是有效的 ProjectOS 项目", response.json()["detail"])

    def test_conversation_can_be_created_and_read_without_frontend(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        created = self.client.post("/api/v1/projects/demo/conversations")

        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["conversation_id"]
        loaded = self.client.get(
            f"/api/v1/projects/demo/conversations/{conversation_id}"
        )

        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["messages"], [])

    def test_runtime_preflight_exposes_docker_image_prerequisite(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})

        response = self.client.get("/api/v1/projects/demo/runtime/preflight")

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()["sandbox"]["status"], {"ready", "image_missing", "invalid_runtime"})

    def test_local_runtime_status_is_visible_without_managed_run(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "demo"})
        self.assertEqual(created.status_code, 201)
        response = self.client.get("/api/v1/projects/demo/runtime/local-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime"]["status"], "not_started")
        self.assertFalse(response.json()["runtime"]["available"])

    def test_dependency_approval_endpoints_expose_and_approve_requirements(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        from app.runtime.manifest import RuntimeManifest

        RuntimeManifest(version=1, profile="python-pip", dependencies_file="requirements.in").save(str(project_path))
        (project_path / "requirements.in").write_text("example==1.0.0\n", encoding="utf-8")

        pending = self.client.get("/api/v1/projects/demo/runtime/dependency-approvals")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["approval"]["status"], "pending")

        class FakeProvisioner:
            def approve_dependencies(self, path: str) -> dict[str, object]:
                return {"status": "ready", "ok": True, "approval": {"status": "approved"}}

        with patch("app.api.app.EnvironmentProvisioner", FakeProvisioner):
            approved = self.client.post("/api/v1/projects/demo/runtime/dependency-approvals/approve")
        self.assertEqual(approved.status_code, 202)
        self.assertIn(approved.json()["status"], {"ready", "dependency_failed", "image_unavailable"})

    def test_capability_listing_is_empty_for_trace_without_waiting_request(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        trace = TraceStore(str(project_path)).start_trace("普通运行")

        response = self.client.get(
            f"/api/v1/projects/demo/runs/{trace.trace_id}/capabilities"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["candidates"], [])

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
