import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef
from app.domain.code.service import CodeIntegrationService, CodeStagingService
from app.agent.code_integration_agent import IntegrationReview
from app.policy.quality import GitCodeIntegrationPolicy
from app.workspace.git_repository import ChangeSet
from app.execution_context import ExecutionContext, ExecutionMode


class CodeGitIntegrationTest(unittest.TestCase):
    def test_integration_review_accepts_only_decision_fields(self) -> None:
        review = IntegrationReview.parse(
            '{"verdict":"approve","rationale":"ChangeSet 与合同一致",'
            '"findings":[],"adapter_requests":[]}'
        )
        self.assertEqual(review.verdict, "approve")
        self.assertEqual(review.findings, ())
        structured = IntegrationReview.parse(
            '{"verdict":"approve","rationale":"记录运行风险",'
            '"findings":[{"code":"runtime.missing","severity":"warning",'
            '"summary":"入口待验证","stage":"preflight","evidence":["compose"]}],'
            '"adapter_requests":[]}'
        )
        self.assertEqual(structured.findings[0].severity, "warning")
        without_adapters = IntegrationReview.parse(
            '{"verdict":"approve","rationale":"无需适配",'
            '"findings":[],"adapter_requests":null}'
        )
        self.assertEqual(without_adapters.adapter_requests, ())
        with self.assertRaises(ValueError):
            IntegrationReview.parse(
                '{"verdict":"needs_adapter","rationale":"需要适配",'
                '"findings":[],"adapter_requests":null}'
            )
        with self.assertRaises(ValueError):
            IntegrationReview.parse(
                '{"verdict":"approve","rationale":"x","code":"print(1)"}'
            )

    def test_integration_review_accepts_boundary_transport_format_characters(self) -> None:
        review = IntegrationReview.parse(
            '\u200b\ufeff {"verdict":"approve","rationale":"ChangeSet 与合同一致",'
            '"findings":[],"adapter_requests":[]}\u2060\n'
        )

        self.assertEqual(review.verdict, "approve")

    def test_integration_review_does_not_extract_json_from_natural_language(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是 JSON"):
            IntegrationReview.parse(
                '审核结论：{"verdict":"approve","rationale":"不应被提取",'
                '"findings":[],"adapter_requests":[]}'
            )

    def test_integration_policy_accepts_required_directory(self) -> None:
        worktree = Path(self.project_path) / "staged"
        (worktree / "workspace" / "tests").mkdir(parents=True)
        (worktree / "workspace" / "tests" / "test_api.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        change = ChangeSet(
            trace_id="trace-code", work_item_id="code-tests",
            branch_name="branch", base_commit="base", commit="commit",
            changed_files=("workspace/tests/test_api.py",),
            worktree_path=str(worktree), required_paths=("tests/",),
        )
        ref = ArtifactRef.staged(
            artifact_key="implementation", trace_id="trace-code",
            work_item_id="code-tests", slot="root",
        )
        report = GitCodeIntegrationPolicy().evaluate(((ref, change),))
        self.assertTrue(report.passed, report.issues)

    def test_integration_policy_accepts_ancestor_baselines_with_control_plane_evidence(self) -> None:
        first_root = Path(self.project_path) / "staged-first"
        second_root = Path(self.project_path) / "staged-second"
        for root, filename in ((first_root, "domain.py"), (second_root, "api.py")):
            target = root / "workspace" / "backend"
            target.mkdir(parents=True)
            (target / filename).write_text("VALUE = 1\n", encoding="utf-8")
        refs_changes = tuple(
            (
                ArtifactRef.staged(
                    artifact_key="implementation", trace_id="trace-code",
                    work_item_id=f"code-{filename[:-3]}", slot="backend",
                ),
                ChangeSet(
                    trace_id="trace-code", work_item_id=f"code-{filename[:-3]}",
                    branch_name="branch", base_commit=base, commit=f"commit-{base}",
                    changed_files=(f"workspace/backend/{filename}",),
                    worktree_path=str(root), required_paths=(f"backend/{filename}",),
                ),
            )
            for root, filename, base in (
                (first_root, "domain.py", "base-0"),
                (second_root, "api.py", "base-1"),
            )
        )
        report = GitCodeIntegrationPolicy().evaluate(
            refs_changes,
            common_baseline="base-2",
            is_ancestor=lambda ancestor, descendant: ancestor in {"base-0", "base-1", "base-2"},
        )
        self.assertTrue(report.passed, report.issues)

    def test_integration_policy_accepts_directory_contract_without_trailing_slash(self) -> None:
        worktree = Path(self.project_path) / "staged-no-slash"
        directory = worktree / "workspace" / "backend" / "app" / "domain"
        directory.mkdir(parents=True)
        (directory / "entities.py").write_text("class Product: pass\n", encoding="utf-8")
        change = ChangeSet(
            trace_id="trace-code", work_item_id="code-domain",
            branch_name="branch", base_commit="base", commit="commit",
            changed_files=("workspace/backend/app/domain/entities.py",),
            worktree_path=str(worktree), required_paths=("backend/app/domain",),
        )
        ref = ArtifactRef.staged(
            artifact_key="implementation", trace_id="trace-code",
            work_item_id="code-domain", slot="backend",
        )
        report = GitCodeIntegrationPolicy().evaluate(((ref, change),))
        self.assertTrue(report.passed, report.issues)

    def test_integration_policy_accepts_directory_glob_contract(self) -> None:
        worktree = Path(self.project_path) / "staged-glob"
        directory = worktree / "workspace" / "backend" / "app" / "domain"
        directory.mkdir(parents=True)
        (directory / "inventory.py").write_text("class Inventory: pass\n", encoding="utf-8")
        change = ChangeSet(
            trace_id="trace-code", work_item_id="code-domain-glob",
            branch_name="branch", base_commit="base", commit="commit",
            changed_files=("workspace/backend/app/domain/inventory.py",),
            worktree_path=str(worktree), required_paths=("backend/app/domain/**",),
        )
        ref = ArtifactRef.staged(
            artifact_key="implementation", trace_id="trace-code",
            work_item_id="code-domain-glob", slot="backend",
        )
        report = GitCodeIntegrationPolicy().evaluate(((ref, change),))
        self.assertTrue(report.passed, report.issues)
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        (Path(self.project_path) / "workspace").mkdir()
        (Path(self.project_path) / "workspace" / "README.md").write_text(
            "generated project\n", encoding="utf-8"
        )
        self.staging = CodeStagingService(self.project_path)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_partition_writes_are_committed_but_not_published_until_integration(self) -> None:
        backend_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-backend",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
        )
        frontend_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-frontend",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="frontend",
        )

        first = self.staging.write_staged_file(
            backend_context, "backend/app.py", "print('first')\n"
        )
        second = self.staging.write_staged_file(
            backend_context, "backend/routes.py", "ROUTES = []\n"
        )
        self.staging.write_staged_file(
            frontend_context, "frontend/index.html", "<main>Todo</main>\n"
        )

        self.assertIn("ChangeSet", first)
        self.assertIn("ChangeSet", second)
        self.assertFalse(
            (Path(self.project_path) / "workspace" / "backend" / "app.py").exists()
        )
        backend_change = self.staging.load_change_set("trace-code", "code-backend")
        self.assertEqual(
            backend_change.changed_files,
            ("workspace/backend/app.py", "workspace/backend/routes.py"),
        )
        self.assertEqual(
            backend_change.base_commit,
            self.staging.load_baseline("trace-code"),
        )

        integration_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-integration",
            agent_id="code_integration_agent",
            execution_mode=ExecutionMode.INTEGRATION,
            input_refs=(
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-code",
                    work_item_id="code-backend",
                    slot="backend",
                ),
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-code",
                    work_item_id="code-frontend",
                    slot="frontend",
                ),
            ),
            publish_target="workspace",
        )
        result = CodeIntegrationService(self.project_path).integrate(integration_context)

        self.assertIn("发布 3 个文件", result)
        self.assertTrue(
            (Path(self.project_path) / "workspace" / "backend" / "app.py").is_file()
        )
        self.assertTrue(
            (Path(self.project_path) / "workspace" / "frontend" / "index.html").is_file()
        )
        summary = (Path(self.project_path) / "implementation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Git 交付记录", summary)
        self.assertIn("workspace/backend/app.py", summary)

        frontend_change = self.staging.load_change_set("trace-code", "code-frontend")
        self.staging.refresh_baseline(
            "trace-code", integrated_commits=(backend_change.commit, frontend_change.commit)
        )
        second = CodeIntegrationService(self.project_path).integrate(integration_context)
        self.assertIn("前置 Wave 合并", second)

    def test_partition_accepts_worktree_relative_path_when_contract_authorizes_prefixed_path(self) -> None:
        context = ExecutionContext(
            trace_id="trace-relative",
            work_item_id="code-domain",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
            allowed_paths=("backend/domain/**",),
        )
        self.staging.write_staged_file(context, "domain/entities.py", "class Product: pass\n")
        change = self.staging.load_change_set("trace-relative", "code-domain")
        self.assertEqual(change.changed_files, ("workspace/backend/domain/entities.py",))

    def test_partition_rejects_file_outside_explicit_owner_set(self) -> None:
        context = ExecutionContext(
            trace_id="trace-owned", work_item_id="code-domain", agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED, slot="backend",
            allowed_paths=("backend/app/domain/**",),
            owned_files=("backend/app/domain/entities.py",),
            implementation_unit_id="backend-domain",
        )
        with self.assertRaises(PermissionError):
            self.staging.write_staged_file(context, "backend/app/domain/errors.py", "class Error: pass\n")

    def test_integration_semantic_gate_detects_missing_local_module(self) -> None:
        worktree = Path(self.project_path) / "semantic"
        (worktree / "workspace" / "backend" / "app" / "domain").mkdir(parents=True)
        (worktree / "workspace" / "backend" / "app" / "domain" / "__init__.py").write_text("\n", encoding="utf-8")
        tests = worktree / "workspace" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_domain.py").write_text("from app.domain.inventory import Inventory\n", encoding="utf-8")
        issues = CodeIntegrationService._validate_local_imports(
            type("Branch", (), {"worktree_path": str(worktree)})()
        )
        self.assertTrue(any("app.domain.inventory" in issue for issue in issues))

    def test_integration_semantic_gate_accepts_existing_backend_module(self) -> None:
        worktree = Path(self.project_path) / "semantic-backend"
        application = worktree / "workspace" / "backend" / "application"
        interfaces = worktree / "workspace" / "backend" / "interfaces"
        application.mkdir(parents=True)
        interfaces.mkdir(parents=True)
        (application / "__init__.py").write_text("\n", encoding="utf-8")
        (application / "dto.py").write_text("class DTO: pass\n", encoding="utf-8")
        (interfaces / "http.py").write_text(
            "from backend.application.dto import DTO\n", encoding="utf-8"
        )
        issues = CodeIntegrationService._validate_local_imports(
            type("Branch", (), {"worktree_path": str(worktree)})()
        )
        self.assertEqual(issues, ())

    def test_integration_semantic_gate_accepts_namespace_package_import(self) -> None:
        worktree = Path(self.project_path) / "semantic-namespace"
        app = worktree / "workspace" / "backend" / "app"
        infrastructure = app / "infrastructure"
        infrastructure.mkdir(parents=True)
        (app / "main.py").write_text(
            "from app.infrastructure import database\n", encoding="utf-8"
        )
        (infrastructure / "database.py").write_text(
            "DATABASE_URL = 'test'\n", encoding="utf-8"
        )
        issues = CodeIntegrationService._validate_local_imports(
            type("Branch", (), {"worktree_path": str(worktree)})()
        )
        self.assertEqual(issues, ())

    def test_project_nested_under_parent_repository_gets_own_git_root(self) -> None:
        nested = Path(self.project_path) / "projects" / "generated"
        nested.mkdir(parents=True)
        (nested / "workspace").mkdir()
        service = CodeStagingService(str(nested))
        context = ExecutionContext(
            trace_id="trace-nested",
            work_item_id="code-nested",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
        )
        service.write_staged_file(context, "backend/app.py", "print('nested')\n")
        self.assertTrue((nested / ".git").exists())

    def test_policy_allows_backend_only_logical_delivery(self) -> None:
        backend_context = ExecutionContext(
            trace_id="trace-policy",
            work_item_id="code-backend",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
        )
        self.staging.write_staged_file(backend_context, "backend/app.py", "print('ok')\n")
        context = ExecutionContext(
            trace_id="trace-policy",
            work_item_id="code-integration",
            agent_id="code_integration_agent",
            execution_mode=ExecutionMode.INTEGRATION,
            input_refs=(
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-policy",
                    work_item_id="code-backend",
                    slot="backend",
                ),
            ),
            publish_target="workspace",
        )
        result = CodeIntegrationService(self.project_path).integrate(context)
        self.assertIn("发布", result)

    def test_policy_requires_contract_entrypoint_and_valid_fastapi_object(self) -> None:
        context = ExecutionContext(
            trace_id="trace-entrypoint",
            work_item_id="code-api",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
            allowed_paths=("backend/app/main.py",),
            required_paths=("backend/app/main.py",),
        )
        self.staging.write_staged_file(
            context,
            "backend/app/main.py",
            "from fastapi import FastAPI\napp = FastAPI()\n",
        )
        integration_context = ExecutionContext(
            trace_id="trace-entrypoint",
            work_item_id="code-integration",
            agent_id="code_integration_agent",
            execution_mode=ExecutionMode.INTEGRATION,
            input_refs=(ArtifactRef.staged(
                artifact_key="implementation",
                trace_id="trace-entrypoint",
                work_item_id="code-api",
                slot="backend",
            ),),
            publish_target="workspace",
        )
        result = CodeIntegrationService(self.project_path).integrate(integration_context)
        self.assertIn("发布 1 个文件", result)


if __name__ == "__main__":
    unittest.main()
