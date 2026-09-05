import json
import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef
from app.domain.architecture.implementation_contract import (
    EntrypointContract,
    InterfaceContract,
    ImplementationContract,
    ImplementationContractStore,
)
from app.domain.architecture.service import ArchitectureService
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.state import RunState
from app.orchestration.task_input import build_task_input
from app.orchestration.trace import TraceContext
from app.orchestration.trace import TraceStore
from app.orchestration.node_result import NodeResult
from app.orchestration.runner import GraphRunner
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.agent.registry import AgentRegistry
from app.tool_manager.gateway import ToolGateway
from app.workflow.compiler import ImplementationContractCompiler
from app.workflow.compiler import TemplateCompiler
from app.workflow.templates import project_delivery_template
from app.domain.code.git_service import GitCodeStagingService


def contract_payload():
    return {
        "schema_version": 1,
        "entrypoints": {
            "backend_file": "backend/app/main.py",
            "backend_import": "app.main:app",
            "backend_command": "uvicorn app.main:app",
            "frontend_file": "frontend/src/main.ts",
        },
        "required_files": ["backend/app/main.py", "frontend/src/main.ts"],
        "implementation_units": [
            {
                "unit_id": "backend-domain",
                "layer": "domain",
                "objective": "实现订单领域模型",
                "allowed_paths": ["workspace/backend/app/domain/**"],
                "required_paths": ["backend/app/domain/order.py"],
                "slot": "backend",
                "acceptance_criteria": ["领域对象可被应用层调用"],
                "policy_refs": ["architecture.layer-boundary.v1"],
                "skill_refs": ["python.domain-model.v1"],
                "owned_files": ["backend/app/domain/order.py"],
                "wave": 0,
            },
            {
                "unit_id": "frontend-shell",
                "layer": "interface",
                "objective": "实现订单页面壳",
                "allowed_paths": ["workspace/frontend/**"],
                "depends_on": ["backend-domain"],
                "slot": "frontend",
                "owned_files": ["frontend/src/main.ts"],
            },
        ],
    }


def root_path_contract_payload():
    return {
        "schema_version": 1,
        "implementation_units": [
            {
                "unit_id": "domain",
                "layer": "domain",
                "objective": "实现领域模型",
                "allowed_paths": ["app/domain/"],
                "required_paths": ["app/domain/entities.py"],
                "owned_files": ["app/domain/entities.py"],
            },
            {
                "unit_id": "tests",
                "layer": "tests",
                "objective": "实现测试",
                "allowed_paths": ["tests/"],
                "required_paths": ["tests/test_domain.py"],
                "owned_files": ["tests/test_domain.py"],
            },
        ],
    }


class ImplementationContractTest(unittest.TestCase):
    def test_interface_kind_aliases_are_normalized_at_contract_boundary(self) -> None:
        payload = {
            "schema_version": 1,
            "interfaces": [{
                "interface_id": "rooms-http",
                "kind": "endpoint",
                "name": "GET /api/rooms",
                "owner_unit": "api",
            }],
            "implementation_units": [{
                "unit_id": "api",
                "layer": "api",
                "objective": "提供房间接口",
                "allowed_paths": ["backend/app/api/routes.py"],
                "owned_files": ["backend/app/api/routes.py"],
                "slot": "backend",
            }],
        }
        contract = ImplementationContract.parse(payload)
        self.assertEqual(contract.interfaces[0].kind, "api")

    def test_common_architecture_interface_kinds_are_normalized(self) -> None:
        for raw_kind, expected in (
            ("rest_api", "api"),
            ("http_handler_factory", "api"),
            ("in_process_repository", "service"),
            ("python_service", "service"),
            ("python-callable", "symbol"),
            ("process-entrypoint", "api"),
            ("worker", "service"),
            ("database", "data"),
        ):
            interface = InterfaceContract(
                interface_id=f"i-{raw_kind}",
                kind=raw_kind,
                name="interface",
                owner_unit="unit",
            )
            self.assertEqual(interface.kind, expected)

    def test_disjoint_owned_files_can_share_a_directory_grant(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [
                {
                    "unit_id": "domain-models",
                    "layer": "domain",
                    "objective": "实现领域模型",
                    "allowed_paths": ["backend/app/domain/**"],
                    "owned_files": ["backend/app/domain/models.py"],
                    "slot": "backend",
                },
                {
                    "unit_id": "domain-errors",
                    "layer": "domain",
                    "objective": "实现领域异常",
                    "allowed_paths": ["backend/app/domain/**"],
                    "owned_files": ["backend/app/domain/errors.py"],
                    "slot": "backend",
                },
            ],
        }
        contract = ImplementationContract.parse(payload)
        self.assertEqual(len(contract.units), 2)

    def test_interface_contract_infers_owner_dependency_and_enters_task_input(self) -> None:
        payload = {
            "schema_version": 1,
            "interfaces": [{
                "interface_id": "inventory-port",
                "kind": "service",
                "name": "InventoryPort.reserve",
                "owner_unit": "domain",
                "signature": "reserve(sku: str, quantity: int) -> None",
            }],
            "implementation_units": [
                {
                    "unit_id": "domain",
                    "layer": "domain",
                    "objective": "定义库存端口",
                    "allowed_paths": ["backend/domain/ports.py"],
                    "owned_files": ["backend/domain/ports.py"],
                    "provides_interfaces": ["inventory-port"],
                },
                {
                    "unit_id": "application",
                    "layer": "application",
                    "objective": "实现库存服务",
                    "allowed_paths": ["backend/application/service.py"],
                    "owned_files": ["backend/application/service.py"],
                    "consumes_interfaces": ["inventory-port"],
                },
            ],
        }
        contract = ImplementationContract.parse(payload)
        self.assertEqual(contract.interfaces[0].name, "InventoryPort.reserve")
        plan = ImplementationContractCompiler().compile(
            contract, goal="库存", plan_id="interface-plan",
            trace=TraceContext(requirement_id="req-interface", trace_id="tr-interface"),
        )
        application = next(item for item in plan.work_items if item.implementation_unit_id == "application")
        domain = next(item for item in plan.work_items if item.implementation_unit_id == "domain")
        self.assertIn(domain.id, application.dependency_ids)
        package = build_task_input(RunState(plan=plan), application)
        self.assertIn("InventoryPort.reserve", package.as_prompt())

    def test_contract_preserves_entrypoints_and_required_files(self) -> None:
        contract = ImplementationContract.parse(contract_payload())
        self.assertEqual(contract.entrypoints.backend_import, "app.main:app")
        self.assertEqual(contract.entrypoints.frontend_file, "frontend/src/main.ts")
        self.assertEqual(contract.required_files, ("backend/app/main.py", "frontend/src/main.ts"))

    def test_compiler_omits_control_plane_denies_but_keeps_business_exclusions(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [{
                "unit_id": "runtime",
                "layer": "runtime",
                "objective": "实现运行时入口",
                "allowed_paths": ["workspace/backend/app/**"],
                "forbidden_paths": [
                    "workspace/**",
                    ".projectos/**",
                    "workspace/backend/app/private.py",
                ],
                "owned_files": ["backend/app/main.py"],
                "slot": "backend",
            }],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="运行时", plan_id="control-plane-denies",
            trace=TraceContext(requirement_id="req-control-plane", trace_id="tr-control-plane"),
        )
        self.assertEqual(plan.work_items[0].forbidden_paths, ("backend/app/private.py",))

    def test_contract_service_reads_published_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ArchitectureService(directory).save_architecture("# 架构设计")
            self.assertEqual(ArchitectureService(directory).load_architecture(), "# 架构设计")

    def test_project_contract_is_persisted_as_one_canonical_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ArchitectureService(directory)
            service.save_architecture("# 架构\n")
            service.save_implementation_contract(json.dumps(contract_payload(), ensure_ascii=False))
            contract = ImplementationContractStore(directory).load()
            self.assertEqual([unit.unit_id for unit in contract.units], ["backend-domain", "frontend-shell"])
            self.assertTrue((Path(directory) / ".projectos/architecture/project-contract.json").is_file())
            self.assertFalse((Path(directory) / ".projectos/architecture/layer-contract.json").exists())
            self.assertEqual(set(contract.layers), {"api", "application", "domain", "infrastructure"})

    def test_service_normalizes_layer_catalog_object_to_canonical_array(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = contract_payload()
            payload["layers"] = {
                "api": {"description": "HTTP adapters"},
                "application": {"description": "use cases"},
                "domain": {"description": "business rules"},
                "infrastructure": {"description": "adapters"},
            }
            ArchitectureService(directory).save_implementation_contract(
                json.dumps(payload, ensure_ascii=False)
            )
            contract = ImplementationContractStore(directory).load()
            self.assertEqual(
                contract.layers, ("api", "application", "domain", "infrastructure")
            )

    def test_service_normalizes_layer_catalog_object_array(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = contract_payload()
            payload["layers"] = [
                {"name": "api", "description": "HTTP adapters"},
                {"id": "application", "description": "use cases"},
                {"layer": "domain"},
                "infrastructure",
            ]
            ArchitectureService(directory).save_implementation_contract(
                json.dumps(payload, ensure_ascii=False)
            )
            self.assertEqual(
                ImplementationContractStore(directory).load().layers,
                ("api", "application", "domain", "infrastructure"),
            )

    def test_layer_validation_error_identifies_top_level_field(self) -> None:
        payload = contract_payload()
        payload["layers"] = {"api": 1}
        with self.assertRaisesRegex(ValueError, r"^layers 必须是字符串数组$"):
            ImplementationContract.parse(payload)

    def test_contract_compiles_scoped_parallel_code_items(self) -> None:
        contract = ImplementationContract.parse(contract_payload())
        trace = TraceContext(requirement_id="req-contract", trace_id="tr-contract")
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付订单系统", plan_id="contract-plan", trace=trace
        )
        self.assertEqual(plan.template_id, "implementation-contract")
        self.assertEqual([item.slot for item in plan.work_items], ["backend", "frontend"])
        self.assertEqual(plan.work_items[1].dependency_ids, ("wi-code-backend-domain",))
        self.assertEqual(plan.work_items[0].execution_mode, ExecutionMode.PARTITIONED)
        self.assertEqual(plan.work_items[0].wave, 0)
        self.assertEqual(plan.work_items[0].owned_files, ("backend/app/domain/order.py",))
        state = RunState(plan=plan)
        package = build_task_input(state, plan.work_items[0])
        self.assertEqual(package.implementation["unit_id"], "backend-domain")
        self.assertIn("architecture.layer-boundary.v1", package.as_prompt())
        self.assertIn("python.domain-model.v1", package.as_prompt())
        self.assertIn("backend/app/main.py", package.as_prompt())

    def test_interface_files_receive_narrow_file_objectives(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [{
                "unit_id": "backend-interfaces",
                "layer": "interfaces",
                "objective": "实现 FastAPI 接口层、路由、schema 和组合根",
                "allowed_paths": ["backend/interfaces/**"],
                "owned_files": [
                    "backend/interfaces/main.py",
                    "backend/interfaces/routes.py",
                    "backend/interfaces/schemas.py",
                ],
                "slot": "backend",
            }],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="接口", plan_id="interface-files",
            trace=TraceContext(requirement_id="req-interface-files", trace_id="tr-interface-files"),
        )
        main = next(item for item in plan.work_items if item.owned_files == ("backend/interfaces/main.py",))
        routes = next(item for item in plan.work_items if item.owned_files == ("backend/interfaces/routes.py",))
        self.assertIn("组合根", main.objective)
        self.assertIn("不得在此文件实现业务用例", main.objective)
        self.assertIn("HTTP 路由适配", routes.objective)

    def test_entrypoint_requirement_stays_on_its_owner_file(self) -> None:
        payload = {
            "schema_version": 1,
            "entrypoints": {"backend_file": "backend/interfaces/main.py"},
            "implementation_units": [{
                "unit_id": "backend-interfaces",
                "layer": "interfaces",
                "objective": "实现接口层",
                "allowed_paths": ["backend/interfaces/**"],
                "owned_files": [
                    "backend/interfaces/main.py",
                    "backend/interfaces/routes.py",
                ],
                "slot": "backend",
            }],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="接口", plan_id="entrypoint-owner",
            trace=TraceContext(requirement_id="req-entrypoint-owner", trace_id="tr-entrypoint-owner"),
        )
        main = next(item for item in plan.work_items if item.owned_files == ("backend/interfaces/main.py",))
        routes = next(item for item in plan.work_items if item.owned_files == ("backend/interfaces/routes.py",))
        self.assertIn("backend/interfaces/main.py", main.required_paths)
        self.assertNotIn("backend/interfaces/main.py", routes.required_paths)

    def test_symbol_summary_keeps_public_signatures_without_full_body(self) -> None:
        source = """
from backend.application.use_cases import CreateThing

class Repository:
    async def save(self, item):
        return item

async def health() -> dict:
    return {\"status\": \"ok\"}
"""
        summary = GitCodeStagingService._symbol_summary(source, "backend/application/ports.py")
        self.assertIn("from backend.application.use_cases import CreateThing", summary)
        self.assertIn("class Repository", summary)
        self.assertIn("async def health", summary)
        self.assertNotIn("return item", summary)

    def test_contract_rejects_directory_globs_in_delivery_fields(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [{
                "unit_id": "deployment-runtime",
                "layer": "deployment",
                "objective": "交付运行配置",
                "allowed_paths": ["workspace/**"],
                "owned_files": ["scripts/**"],
                "slot": "root",
            }],
        }
        with self.assertRaisesRegex(ValueError, "owned_files.*具体文件"):
            ImplementationContract.parse(payload)

    def test_multi_file_unit_expands_to_one_concrete_file_per_work_item(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [{
                "unit_id": "deployment-runtime",
                "layer": "deployment",
                "objective": "交付运行配置",
                "allowed_paths": ["workspace/**"],
                "owned_files": ["docker-compose.yml", ".env.example"],
                "slot": "root",
            }],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="部署", plan_id="deployment-plan",
            trace=TraceContext(requirement_id="req-deployment", trace_id="tr-deployment"),
        )
        self.assertEqual(
            [item.owned_files for item in plan.work_items],
            [("docker-compose.yml",), (".env.example",)],
        )
        self.assertEqual({item.slot for item in plan.work_items}, {"root"})
        for item in plan.work_items:
            self.assertRegex(item.id, r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

    def test_contract_rejects_required_globs(self) -> None:
        payload = contract_payload()
        payload["implementation_units"][0]["required_paths"] = ["backend/app/domain/**"]
        with self.assertRaisesRegex(ValueError, "required_paths.*具体文件"):
            ImplementationContract.parse(payload)

    def test_compiled_code_work_items_have_one_concrete_owned_file(self) -> None:
        contract = ImplementationContract.parse(contract_payload())
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付", plan_id="single-file", trace=TraceContext(
                requirement_id="req-single-file", trace_id="tr-single-file"
            )
        )
        for item in plan.work_items:
            self.assertEqual(item.agent_id, "code_agent")
            self.assertEqual(len(item.owned_files), 1)

    def test_compiler_exposes_predecessor_changeset_as_authorized_input(self) -> None:
        contract = ImplementationContract.parse(contract_payload())
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付订单系统", plan_id="contract-plan", trace=TraceContext(requirement_id="req-contract", trace_id="tr-contract")
        )
        dependent = plan.work_items[1]
        self.assertIn("staged:tr-contract:wi-code-backend-domain:backend", {ref.ref_id for ref in dependent.input_refs})

    def test_compiler_normalizes_legacy_architecture_input_alias(self) -> None:
        payload = contract_payload()
        payload["implementation_units"][0]["input_refs"] = [
            "todo-architecture-module-runtime"
        ]
        contract = ImplementationContract.parse(payload)
        plan = ImplementationContractCompiler().compile(
            contract,
            goal="兼容旧架构引用",
            plan_id="legacy-input-alias",
            trace=TraceContext(requirement_id="req-alias", trace_id="tr-alias"),
        )
        first = plan.work_items[0]
        self.assertIn("published:architecture:current", {ref.ref_id for ref in first.input_refs})

    def test_wave_is_a_barrier_while_same_wave_units_remain_parallel(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [
                {"unit_id": "domain-a", "layer": "domain", "objective": "a", "allowed_paths": ["backend/app/domain/a.py"], "owned_files": ["backend/app/domain/a.py"], "wave": 0, "slot": "backend"},
                {"unit_id": "domain-b", "layer": "domain", "objective": "b", "allowed_paths": ["backend/app/domain/b.py"], "owned_files": ["backend/app/domain/b.py"], "wave": 0, "slot": "backend"},
                {"unit_id": "application", "layer": "application", "objective": "app", "allowed_paths": ["backend/app/application/app.py"], "owned_files": ["backend/app/application/app.py"], "wave": 1, "slot": "backend"},
            ],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="分层", plan_id="wave-plan",
            trace=TraceContext(requirement_id="req-wave", trace_id="tr-wave"),
        )
        a, b, application = plan.work_items
        self.assertEqual(a.dependency_ids, ())
        self.assertEqual(b.dependency_ids, ())
        self.assertEqual(set(application.dependency_ids), {a.id, b.id})

    def test_dependency_promotes_inconsistent_declared_wave(self) -> None:
        """A consumer cannot remain in an earlier wave than its provider."""
        payload = {
            "schema_version": 1,
            "interfaces": [{
                "interface_id": "http-api",
                "kind": "api",
                "name": "HTTP API",
                "owner_unit": "api",
            }],
            "implementation_units": [
                {
                    "unit_id": "api",
                    "layer": "api",
                    "objective": "提供 HTTP API",
                    "allowed_paths": ["backend/app/api/routes.py"],
                    "owned_files": ["backend/app/api/routes.py"],
                    "provides_interfaces": ["http-api"],
                    "wave": 3,
                    "slot": "backend",
                },
                {
                    "unit_id": "frontend",
                    "layer": "frontend",
                    "objective": "调用 HTTP API",
                    "allowed_paths": ["frontend/app.js"],
                    "owned_files": ["frontend/app.js"],
                    "consumes_interfaces": ["http-api"],
                    # Deliberately inconsistent: compiler must promote it.
                    "wave": 1,
                    "slot": "frontend",
                },
            ],
        }
        plan = ImplementationContractCompiler().compile(
            ImplementationContract.parse(payload), goal="前端", plan_id="wave-promotion",
            trace=TraceContext(requirement_id="req-wave-promotion", trace_id="tr-wave-promotion"),
        )
        api, frontend = plan.work_items
        self.assertGreaterEqual(frontend.wave, api.wave + 1)
        self.assertEqual(frontend.dependency_ids, (api.id,))

    def test_contract_normalizes_logical_paths_to_physical_partitions(self) -> None:
        contract = ImplementationContract.parse(root_path_contract_payload())
        trace = TraceContext(requirement_id="req-root", trace_id="tr-root")
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付分层项目", plan_id="root-plan", trace=trace
        )
        domain, tests = plan.work_items
        self.assertEqual(domain.slot, "backend")
        self.assertEqual(domain.allowed_paths, ("backend/app/domain/",))
        self.assertEqual(domain.required_paths, ("backend/app/domain/entities.py",))
        self.assertEqual(tests.slot, "root")
        self.assertEqual(tests.allowed_paths, ("tests/",))

    def test_contract_rejects_cycles(self) -> None:
        payload = contract_payload()
        payload["implementation_units"][0]["depends_on"] = ["frontend-shell"]
        with self.assertRaises(ValueError):
            ImplementationContract.parse(payload)

    def test_contract_normalizes_scalar_refs_and_empty_optional_fields(self) -> None:
        payload = contract_payload()
        payload["implementation_units"][0]["policy_refs"] = "architecture.layer-boundary.v1"
        payload["implementation_units"][0]["skill_refs"] = ""
        payload["implementation_units"][0]["parallel_group"] = ""
        contract = ImplementationContract.parse(payload)
        self.assertEqual(contract.units[0].policy_refs, ("architecture.layer-boundary.v1",))
        self.assertEqual(contract.units[0].skill_refs, ())
        self.assertIsNone(contract.units[0].parallel_group)

    def test_project_documents_only_requires_upstream_documents(self) -> None:
        payload = {
            "schema_version": 1,
            "implementation_units": [{
                "unit_id": "project-documents",
                "layer": "delivery",
                "objective": "维护项目文档",
                "allowed_paths": ["requirement.md", "architecture.md", "architecture_contract.md", "tasks.md", "environment.md", "implementation.md", "tests.md", "review.md"],
                "required_paths": ["requirement.md", "architecture.md", "architecture_contract.md", "tasks.md", "environment.md", "implementation.md", "tests.md", "review.md"],
                "slot": "root",
            }],
        }
        contract = ImplementationContract.parse(payload)
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付文档", plan_id="docs-plan",
            trace=TraceContext(requirement_id="req-docs", trace_id="tr-docs"),
        )
        self.assertEqual(
            plan.work_items[0].required_paths,
            ("requirement.md", "architecture.md", "architecture_contract.md", "tasks.md"),
        )

    def test_project_delivery_expands_contract_into_parallel_code_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            traces = TraceStore(directory)
            trace = traces.start_trace("交付订单系统")
            ImplementationContractStore(directory).save(contract_payload())
            agents = {
                "requirement_agent", "architecture_agent", "architecture_contract_agent",
                "task_agent", "bootstrap_agent", "code_integration_agent", "test_agent",
                "review_agent",
            }
            plan = TemplateCompiler().compile(
                project_delivery_template(),
                goal="交付订单系统",
                plan_id="delivery",
                trace=trace,
                agent_output_keys={agent: agent for agent in agents},
            )
            state = RunState(plan=plan)
            contract_item = plan.work_item("wi-03-architecture-contract")
            assert contract_item is not None
            state.record(
                contract_item,
                NodeResult.completed(
                    work_item_id=contract_item.id,
                    agent_id=contract_item.agent_id,
                    content="合同已发布",
                ),
            )
            GraphRunner(AgentRegistry(), ToolGateway(), traces=traces)._expand_implementation_plan_if_ready(state)

            code_items = [item for item in state.plan.work_items if item.agent_id == "code_agent"]
            integration = next(item for item in state.plan.work_items if item.agent_id == "code_integration_agent")
            self.assertEqual([item.implementation_unit_id for item in code_items], ["backend-domain", "frontend-shell"])
            self.assertEqual(set(integration.dependency_ids), {item.id for item in code_items})
            self.assertEqual({ref.work_item_id for ref in integration.input_refs}, {item.id for item in code_items})
            self.assertEqual(len([item for item in state.plan.work_items if item.id == "implementation"]), 0)
            self.assertEqual([item.implementation_unit_id for item in traces.load_plan(trace.trace_id).work_items if item.agent_id == "code_agent"], ["backend-domain", "frontend-shell"])

            # Phase 6 hand-off: replacing the template's single implementation
            # placeholder must not detach the fixed downstream delivery stages.
            tasks = next(item for item in state.plan.work_items if item.id.endswith("-tasks-plan"))
            environment = next(item for item in state.plan.work_items if item.id.endswith("-environment"))
            tests = next(item for item in state.plan.work_items if item.id.endswith("-tests"))
            review = next(item for item in state.plan.work_items if item.id.endswith("-review"))
            self.assertIsNotNone(tasks)
            self.assertIsNotNone(environment)
            self.assertIsNotNone(tests)
            self.assertIsNotNone(review)
            assert tasks is not None and environment is not None
            assert tests is not None and review is not None
            self.assertIn(contract_item.id, tasks.dependency_ids)
            quality = next(item for item in state.plan.work_items if item.id.endswith("-tasks-quality-gate"))
            self.assertIn(quality.id, environment.dependency_ids)
            self.assertIn(integration.id, tests.dependency_ids)
            self.assertIn(tests.id, review.dependency_ids)

    def test_dynamic_plan_without_template_id_expands_contract_into_code_items(self) -> None:
        """Phase 5 must work for a Planner-generated DAG, not only templates."""
        with tempfile.TemporaryDirectory() as directory:
            traces = TraceStore(directory)
            trace = traces.start_trace("动态合同交付")
            contract = ImplementationContract.parse(contract_payload())
            ImplementationContractStore(directory).save(contract.as_dict())
            contract_item = WorkItem(
                id="wi-contract",
                agent_id="architecture_contract_agent",
                objective="保存实现合同",
                output_key="architecture_contract",
                artifact_key="architecture_contract",
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture_contract",
            )
            integration = WorkItem(
                id="wi-code-integration",
                agent_id="code_integration_agent",
                objective="整合代码分区",
                output_key="implementation_merge",
                artifact_key="implementation",
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="workspace",
                dependencies=(WorkItemDependency("wi-contract", DependencySource.SYSTEM),),
            )
            plan = ExecutionPlan(
                id="dynamic-contract-plan",
                goal="动态合同交付",
                work_items=(contract_item, integration),
                template_id=None,
                trace=trace,
            )
            state = RunState(plan=plan)
            state.record(
                contract_item,
                NodeResult.completed(
                    work_item_id=contract_item.id,
                    agent_id=contract_item.agent_id,
                    content="合同已发布",
                ),
            )
            runner = GraphRunner(
                AgentRegistry(),
                ToolGateway(),
                traces=traces,
            )
            runner._expand_implementation_plan_if_ready(state)

            code_items = [item for item in state.plan.work_items if item.agent_id == "code_agent"]
            self.assertEqual(
                {item.implementation_unit_id for item in code_items},
                {"backend-domain", "frontend-shell"},
            )
            merged = state.plan.work_item("wi-code-integration")
            assert merged is not None
            self.assertEqual(set(merged.dependency_ids), {item.id for item in code_items})

    def test_review_artifact_unit_is_left_to_review_agent(self) -> None:
        payload = contract_payload()
        payload["implementation_units"].append({
            "unit_id": "u-review",
            "layer": "operations",
            "objective": "生成最终 review.md",
            "allowed_paths": ["review.md"],
            "required_paths": ["review.md"],
            "owned_files": ["review.md"],
            "slot": "root",
        })
        contract = ImplementationContract.parse(payload)
        plan = ImplementationContractCompiler().compile(
            contract, goal="交付", plan_id="review-filter", trace=TraceContext(requirement_id="r", trace_id="t")
        )
        self.assertNotIn("u-review", {item.implementation_unit_id for item in plan.work_items})


if __name__ == "__main__":
    unittest.main()
