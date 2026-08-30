import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.application.runs import StartedRun
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

    def test_llm_provider_catalog_is_available_for_user_selection(self) -> None:
        response = self.client.get("/api/v1/llm/providers")

        self.assertEqual(response.status_code, 200)
        providers = {item["id"]: item for item in response.json()["providers"]}
        self.assertIn("siliconflow", providers)
        self.assertEqual(providers["siliconflow"]["model"], "deepseek-ai/DeepSeek-V4-Pro")
        self.assertIn("fhl", providers)
        self.assertEqual(providers["fhl"]["model"], "gpt-5.6-terra")
        self.assertNotIn("api_key", providers["siliconflow"])

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
                    "id": "architecture_layered",
                    "name": "三层结构化架构设计",
                    "description": "总体蓝图、模块设计、实现准备三层架构对象并行与集成。",
                },
                {
                    "id": "project_delivery",
                    "name": "项目交付草案",
                    "description": "生成需求、架构合同、实施任务、并行代码分区、测试证据和审查报告。",
                },
                {
                    "id": "project_delivery_minimal",
                    "name": "最小项目交付",
                    "description": "使用已有需求和架构，生成任务、环境、并行代码、测试和审查结果。",
                },
            ],
        )

    def test_agent_skill_configuration_is_project_scoped(self) -> None:
        created = self.client.post("/api/v1/projects", json={"name": "demo"})
        self.assertEqual(created.status_code, 201)

        available = self.client.get("/api/v1/projects/demo/skills")
        self.assertEqual(available.status_code, 200)
        refs = {item["ref"] for item in available.json()["skills"]}
        self.assertIn("python.http-service.v1", refs)

        updated = self.client.put(
            "/api/v1/projects/demo/agents/code_agent/skills",
            json={"refs": ["python.http-service.v1", "security.baseline.v1"]},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["refs"], ["python.http-service.v1", "security.baseline.v1"])

        loaded = self.client.get("/api/v1/projects/demo/agents/code_agent/skills")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["refs"], ["python.http-service.v1", "security.baseline.v1"])

        unknown = self.client.put(
            "/api/v1/projects/demo/agents/code_agent/skills",
            json={"refs": ["missing.skill.v1"]},
        )
        self.assertEqual(unknown.status_code, 422)

        custom = self.client.put(
            "/api/v1/projects/demo/skills/company.review.v1",
            json={"content": "# Company Review\n\n必须保留审计证据"},
        )
        self.assertEqual(custom.status_code, 200)
        available_after = self.client.get("/api/v1/projects/demo/skills")
        self.assertIn(
            "company.review.v1",
            {item["ref"] for item in available_after.json()["skills"]},
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
        self.assertIn("project", response.json())

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

    def test_run_progress_endpoint_reads_persisted_worker_snapshot(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        trace = TraceStore(str(project_path)).start_trace("进度查询")
        from app.orchestration.progress import WorkerProgressStore

        WorkerProgressStore(str(project_path)).write(
            trace.trace_id,
            {
                "trace_id": trace.trace_id,
                "phase": "llm_streaming",
                "event": "llm_chunk_received",
                "last_progress_at": "2026-08-22T00:00:00+00:00",
                "counters": {"llm_chunks": 3},
            },
        )
        response = self.client.get(
            f"/api/v1/projects/demo/runs/{trace.trace_id}/progress"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress"]["phase"], "llm_streaming")

    def test_resume_accepts_empty_json_body(self) -> None:
        self.client.post("/api/v1/projects", json={"name": "demo"})
        project_path = Path(self._directory.name) / "demo"
        trace = TraceStore(str(project_path)).start_trace("恢复测试")
        with patch.object(
            self.client.app.state.run_service,
            "resume_run",
            return_value=StartedRun(
                trace_id=trace.trace_id,
                plan_id="plan-1",
                workflow_id="project_delivery",
                status="running",
            ),
        ):
            response = self.client.post(
                f"/api/v1/projects/demo/runs/{trace.trace_id}/resume", json={}
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["trace_id"], trace.trace_id)

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
