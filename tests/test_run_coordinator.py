import json
import tempfile
import time
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.application.runs import (
    RunCoordinator,
    _load_delivery_resume,
    _canonical_code_recovery_diagnostic,
    _prepare_architecture_contract_recovery,
    _stall_observations,
    _renew_worker_deadline,
)
from app.bootstrap.runtime import build_container
from app.execution_context import ExecutionMode
from app.orchestration.node_result import NodeResult
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunResult, GraphRunStatus
from app.orchestration.state import RunState
from app.orchestration.trace import TraceStore
from app.orchestration.progress import (
    ExecutionActivity,
    ExecutionLifecycle,
    ProgressSignalKind,
    WorkerProgressStore,
)
from app.orchestration.retry import (
    FailureKind,
    FailureSignal,
    RecoveryAction,
    RetryLedger,
    RetryLimits,
    RetryPolicy,
    RetryRecord,
    RetryScope,
)
from app.orchestration.work_item import (
    DependencySource,
    WorkItem,
    WorkItemDependency,
)
from app.project.project import Project
from app.planner.service import PlannerFailure


class FakeProcess:
    def __init__(self, *, alive: bool, exitcode: int | None = 0) -> None:
        self.alive = alive
        self.exitcode = exitcode
        self.started = False
        self.terminated = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True


class NonRetryableRepairTest(unittest.TestCase):
    def test_nonretryable_plan_error_never_calls_repair_planner(self):
        item = WorkItem(id="architecture", agent_id="architecture_agent",
                        objective="Design", output_key="architecture")
        plan = ExecutionPlan(id="plan", goal="Design", work_items=(item,))
        signal = FailureSignal(FailureKind.PLANNER_VALIDATION, "Upstream must be repaired",
                               retryable=False, validator="ArchitectureInputPreflight")
        node = NodeResult.needs_replan(work_item_id=item.id, agent_id=item.agent_id,
                                      content=signal.summary, signal=signal)
        result = GraphRunResult(status=GraphRunStatus.NEEDS_REPLAN, state=RunState(plan),
                                node_result=node, failure_signal=signal)
        container = SimpleNamespace(runner=Mock(), planner=Mock(), traces=Mock())
        container.runner.run.return_value = result
        self.assertIs(RunCoordinator._run_with_repairs(container, plan), result)
        container.runner.run.assert_called_once()
        container.planner.plan_repair.assert_not_called()
        self.assertEqual(container.traces.record_event.call_args.args[2], "repair_cycle_blocked")


class CodeWaveRepairFrontierTest(unittest.TestCase):
    def test_owner_rerun_invalidates_integration_and_review_descendants(self) -> None:
        owner = WorkItem(id="wi-http", agent_id="code_agent", objective="HTTP",
                         output_key="http", owned_files=("backend/app/main.py",))
        independent = WorkItem(id="wi-service", agent_id="code_agent", objective="Service",
                               output_key="service", owned_files=("backend/app/task_service.py",))
        integration = WorkItem(
            id="wi-integration", agent_id="code_integration_agent", objective="Integrate",
            output_key="implementation", dependencies=(WorkItemDependency(
                work_item_id=owner.id, source=DependencySource.SYSTEM,
            ), WorkItemDependency(
                work_item_id=independent.id, source=DependencySource.SYSTEM,
            )),
        )
        review = WorkItem(
            id="wi-review", agent_id="review_agent", objective="Review",
            output_key="review", dependencies=(WorkItemDependency(
                work_item_id=integration.id, source=DependencySource.SYSTEM,
            ),),
        )
        plan = ExecutionPlan(id="delivery-frontier", goal="todo",
                             work_items=(integration, owner, independent, review))
        failure = FailureSignal(
            FailureKind.CODE_DELIVERY_INCOMPLETE,
            "Integration 语义检查拒绝合并 [owner_files: backend/app/main.py]",
            related_work_item_ids=(owner.id,),
        )
        failed = NodeResult.needs_replan(
            work_item_id=integration.id, agent_id=integration.agent_id,
            content=failure.summary, signal=failure,
        )
        state = RunState(plan=plan, node_results={
            item.id: NodeResult.completed(
                work_item_id=item.id, agent_id=item.agent_id, content=item.id,
            ) for item in (owner, independent, review)
        } | {integration.id: failed})
        first = GraphRunResult(
            status=GraphRunStatus.NEEDS_REPLAN, state=state,
            node_result=failed, failure_signal=failure,
        )
        second = GraphRunResult(status=GraphRunStatus.BLOCKED, state=state)
        container = SimpleNamespace(runner=Mock(), planner=Mock(), traces=Mock())
        container.runner.run.side_effect = [first, second]
        self.assertIs(RunCoordinator._run_with_repairs(container, plan), second)
        self.assertEqual(set(state.node_results), {independent.id})
        self.assertEqual(state.forced_rerun_work_item_ids, {owner.id})
        self.assertIn(owner.id, state.recovery_diagnostics)
        self.assertEqual(container.runner.run.call_count, 2)



class CanonicalCodeRecoveryTest(unittest.TestCase):
    def test_old_contract_derives_bound_import_without_changing_scope(self) -> None:
        provider = WorkItem(
            id="wi-service", agent_id="code_agent", objective="Service",
            output_key="service", implementation_unit_id="service",
            owned_files=("backend/app/task_service.py",),
            delivery_contract={"provided_symbols": ["TaskService", "prose and noise"]},
        )
        consumer = WorkItem(
            id="wi-http", agent_id="code_agent", objective="HTTP",
            output_key="http", owned_files=("backend/app/main.py",),
            dependencies=(WorkItemDependency(
                work_item_id=provider.id, source=DependencySource.SYSTEM,
            ),),
        )
        plan = ExecutionPlan(
            id="old-contract", goal="todo", work_items=(consumer, provider),
        )
        diagnostic = _canonical_code_recovery_diagnostic(plan, consumer.id)
        self.assertIn("backend/app/task_service.py -> app.task_service", diagnostic)
        self.assertIn("TaskService", diagnostic)
        self.assertNotIn("prose and noise", diagnostic)
        self.assertEqual(_canonical_code_recovery_diagnostic(plan, provider.id), "")
        self.assertNotIn("required_bindings", consumer.delivery_contract or {})


class ResumeCheckpointMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.container = build_container(self.directory.name)
        self.trace = self.container.traces.start_trace("合并交付恢复 checkpoint")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_event_frontier_wins_over_stale_forced_checkpoint(self) -> None:
        environment = WorkItem(
            id="wi-environment",
            agent_id="bootstrap_agent",
            objective="准备运行环境",
            output_key="environment",
        )
        implementation = WorkItem(
            id="wi-implementation",
            agent_id="code_agent",
            objective="实现代码",
            output_key="implementation",
            dependencies=(
                WorkItemDependency(
                    work_item_id=environment.id,
                    source=DependencySource.SYSTEM,
                ),
            ),
        )
        plan = ExecutionPlan(
            id="delivery-merge",
            goal="验证交付恢复",
            trace=self.trace,
            work_items=(environment, implementation),
        )
        self.container.traces.record_plan(plan)
        self.container.traces.record_delivery_plan(plan)

        # This models a stale live checkpoint left by a repair barrier: it
        # carries a forced rerun marker but omits upstream completions that
        # are already durable in the event log.
        stale = RunState(
            plan=plan,
            forced_rerun_work_item_ids={implementation.id},
            recovery_diagnostics={implementation.id: "repair scheduled"},
        )
        self.container.traces.record_checkpoint(self.trace, stale.as_checkpoint())

        completed = RunState(
            plan=plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id,
                    agent_id=item.agent_id,
                    content=item.id,
                )
                for item in plan.work_items
            },
        )
        self.container.traces.record_delivery_checkpoint(
            self.trace, completed.as_checkpoint()
        )
        for item in plan.work_items:
            self.container.traces.record_event(
                self.trace, item.id, "work_item_completed"
            )

        resumed_plan, resumed = _load_delivery_resume(
            self.container, self.trace.trace_id
        )

        self.assertEqual(resumed_plan.id, plan.id)
        self.assertEqual(set(resumed.node_results), {environment.id, implementation.id})
        self.assertEqual(resumed.forced_rerun_work_item_ids, set())
        self.assertEqual(resumed.recovery_diagnostics, {})


    def test_resume_invalidates_false_completed_code_integration(self) -> None:
        from pathlib import Path
        producer = WorkItem(
            id="wi-http", agent_id="code_agent", objective="HTTP", output_key="http",
        )
        integration = WorkItem(
            id="wi-integrate", agent_id="code_integration_agent",
            objective="集成", output_key="implementation",
            dependencies=(WorkItemDependency(
                work_item_id=producer.id, source=DependencySource.SYSTEM,
            ),),
        )
        plan = ExecutionPlan(
            id="delivery-false-integration", goal="检查历史修复产物",
            trace=self.trace, work_items=(integration, producer),
        )
        main = Path(self.directory.name) / "workspace/backend/app/main.py"
        main.parent.mkdir(parents=True)
        main.write_text(
            "from app.missing_service import Service\n", encoding="utf-8"
        )
        self.container.traces.record_plan(plan)
        self.container.traces.record_delivery_plan(plan)
        complete = RunState(plan=plan, node_results={
            item.id: NodeResult.completed(
                work_item_id=item.id, agent_id=item.agent_id, content=item.id,
            ) for item in plan.work_items
        })
        self.container.traces.record_delivery_checkpoint(
            self.trace, complete.as_checkpoint()
        )
        _, resumed = _load_delivery_resume(self.container, self.trace.trace_id)
        self.assertEqual(set(resumed.node_results), {producer.id})
        self.assertIn("不存在的本地模块", resumed.recovery_diagnostics[integration.id])

    def test_resume_preserves_completed_dependency_after_its_anchor(self) -> None:
        producer = WorkItem(
            id="wi-code-producer", agent_id="code_agent", objective="实现服务",
            output_key="implementation_service",
        )
        integration = WorkItem(
            id="wi-integration-anchor", agent_id="code_integration_agent",
            objective="集成服务", output_key="implementation",
            dependencies=(WorkItemDependency(
                work_item_id=producer.id, source=DependencySource.SYSTEM,
            ),),
        )
        # Dynamically expanded plans retain template anchors first. The
        # checkpoint is complete even though the producer is serialized later.
        plan = ExecutionPlan(
            id="delivery-out-of-order", goal="恢复已完成集成",
            trace=self.trace, work_items=(integration, producer),
        )
        self.container.traces.record_plan(plan)
        self.container.traces.record_delivery_plan(plan)
        complete = RunState(
            plan=plan,
            node_results={item.id: NodeResult.completed(
                work_item_id=item.id, agent_id=item.agent_id, content=item.id,
            ) for item in plan.work_items},
        )
        self.container.traces.record_delivery_checkpoint(
            self.trace, complete.as_checkpoint()
        )
        resumed_plan, resumed = _load_delivery_resume(
            self.container, self.trace.trace_id
        )
        self.assertEqual(resumed_plan.id, plan.id)
        self.assertEqual(set(resumed.node_results), {integration.id, producer.id})


class ArchitectureContractRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.container = build_container(self.directory.name)
        self.trace = self.container.traces.start_trace("架构合同恢复")
        dependency = lambda item_id: WorkItemDependency(
            work_item_id=item_id, source=DependencySource.SYSTEM
        )
        self.blueprint = WorkItem(
            id="wi-architecture-blueprint",
            agent_id="architecture_agent",
            stage_id="architecture_blueprint",
            objective="产出总体蓝图",
            output_key="architecture_blueprint",
            artifact_key="architecture",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="blueprint",
        )
        self.dependency_module = WorkItem(
            id="wi-module-domain",
            agent_id="architecture_agent",
            stage_id="architecture_module",
            objective="产出 domain 模块设计",
            output_key="architecture_module_domain",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="module-domain",
            dependencies=(dependency(self.blueprint.id),),
            delivery_contract={
                "architecture": {
                    "depth": 1,
                    "module_id": "domain",
                    "depends_on_modules": [],
                }
            },
        )
        self.module_api = WorkItem(
            id="wi-module-api",
            agent_id="architecture_agent",
            stage_id="architecture_module",
            objective="产出 api 模块设计",
            output_key="architecture_module_api",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="module-api",
            dependencies=(dependency(self.blueprint.id),),
            delivery_contract={
                "architecture": {
                    "depth": 1,
                    "module_id": "api",
                    "depends_on_modules": ["domain"],
                }
            },
        )
        self.owner = WorkItem(
            id="wi-implementation-api",
            agent_id="architecture_agent",
            objective="产出 api 实现设计",
            output_key="architecture_implementation_api",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="implementation-module-api",
            dependencies=(dependency(self.module_api.id),),
            delivery_contract={
                "architecture": {
                    "depth": 2,
                    "module_id": "api",
                    "depends_on_modules": ["domain"],
                }
            },
        )
        self.integration = WorkItem(
            id="wi-architecture-integration",
            agent_id="architecture_agent",
            objective="集成架构设计",
            output_key="architecture_candidate",
            execution_mode=ExecutionMode.INTEGRATION,
            publish_target="architecture",
            dependencies=(
                dependency(self.dependency_module.id),
                dependency(self.module_api.id),
                dependency(self.owner.id),
            ),
        )
        self.descendant = WorkItem(
            id="wi-after-architecture",
            agent_id="task_agent",
            objective="生成任务",
            output_key="tasks",
            dependencies=(dependency(self.integration.id),),
        )
        self.unrelated = WorkItem(
            id="wi-unrelated",
            agent_id="requirement_agent",
            objective="保留的上游节点",
            output_key="requirement",
        )
        self.plan = ExecutionPlan(
            id="architecture-contract-recovery",
            goal="验证架构合同局部恢复",
            trace=self.trace,
            work_items=(
                self.unrelated,
                self.blueprint,
                self.dependency_module,
                self.module_api,
                self.owner,
                self.integration,
                self.descendant,
            ),
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_invalidates_only_unique_owner_and_descendants(self) -> None:
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.dependency_module.id,
            artifact_key="architecture",
            slot=self.dependency_module.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "module-domain-v1",
                    "depth": 1,
                    "parent_design_id": "blueprint-v1",
                    "module_id": "domain",
                    "responsibilities": ["维护库存"],
                    "provided_interfaces": [
                        {
                            "interface_id": "domain.issue_stock",
                            "direction": "provided",
                            "summary": "扣减指定 SKU 的库存",
                        }
                    ],
                    "consumed_interfaces": [],
                    "entities": ["Stock"],
                    "depends_on_modules": [],
                    "acceptance_criteria": [],
                    "requirement_ids": [],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 实现设计 api 引用了未声明的接口: "
                    "domain.ship_stock"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertIn(self.unrelated.id, recovered.node_results)
        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertNotIn(self.descendant.id, recovered.node_results)
        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.owner.id})
        diagnostic = recovered.recovery_diagnostics[self.owner.id]
        self.assertIn("domain.ship_stock", diagnostic)
        self.assertIn("domain.issue_stock", diagnostic)
        self.assertNotIn("是 implementation unit ID", diagnostic)
        self.assertEqual(
            self.owner.contract_digest,
            self.plan.work_item(self.owner.id).contract_digest,
        )
        event = self.container.traces.list_events(self.trace.trace_id)[-1]
        self.assertEqual(event["type"], "architecture_contract_recovery_scheduled")
        self.assertEqual(event["details"]["rerun_work_item_id"], self.owner.id)
        self.assertEqual(
            event["details"]["invalidated_work_item_ids"],
            sorted((self.owner.id, self.integration.id, self.descendant.id)),
        )

    def test_does_not_schedule_for_non_contract_failure(self) -> None:
        state = RunState(
            plan=self.plan,
            node_results={
                self.owner.id: NodeResult.completed(
                    work_item_id=self.owner.id,
                    agent_id=self.owner.agent_id,
                    content="runtime",
                )
            },
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={"error": "provider_transport_failure"},
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertIn(self.owner.id, recovered.node_results)
        self.assertEqual(recovered.forced_rerun_work_item_ids, set())
        self.assertEqual(len(self.container.traces.list_events(self.trace.trace_id)), 1)

    def test_invalidates_module_design_with_mismatched_blueprint_purpose(self) -> None:
        purpose = "为客户端提供库存 HTTP 操作和健康检查的访问边界，并将领域结果转换为 HTTP 响应。"
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.blueprint.id,
            artifact_key="architecture",
            slot=self.blueprint.slot or "blueprint",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "blueprint-v1",
                    "depth": 0,
                    "system_boundary": "库存 HTTP 服务",
                    "layers": [{"name": "api"}, {"name": "domain"}],
                    "modules": [
                        {
                            "module_id": "domain",
                            "responsibility": "维护库存",
                            "purpose": "维护 SKU 库存状态和库存业务规则。",
                            "depends_on_modules": [],
                        },
                        {
                            "module_id": "api",
                            "responsibility": "提供 HTTP 访问边界",
                            "purpose": purpose,
                            "depends_on_modules": ["domain"],
                        },
                    ],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 模块设计 api 的 purpose 必须与 Blueprint 一致"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertIn(self.blueprint.id, recovered.node_results)
        self.assertIn(self.dependency_module.id, recovered.node_results)
        self.assertNotIn(self.module_api.id, recovered.node_results)
        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertNotIn(self.descendant.id, recovered.node_results)
        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.module_api.id})
        diagnostic = recovered.recovery_diagnostics[self.module_api.id]
        self.assertIn("模块设计 api", diagnostic)
        self.assertIn(purpose, diagnostic)
        event = self.container.traces.list_events(self.trace.trace_id)[-1]
        self.assertEqual(event["type"], "architecture_contract_recovery_scheduled")
        self.assertEqual(event["details"]["rerun_work_item_id"], self.module_api.id)
        self.assertEqual(
            event["details"]["invalidated_work_item_ids"],
            sorted(
                (
                    self.module_api.id,
                    self.owner.id,
                    self.integration.id,
                    self.descendant.id,
                )
            ),
        )

    def test_invalidates_blueprint_when_required_file_has_no_implementation_owner(self) -> None:
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 1 validation error for ArchitectureDesignBundle\n"
                    "  Value error, ArchitectureBlueprint.required_file_not_owned: "
                    "backend/app/__init__.py"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertIn(self.unrelated.id, recovered.node_results)
        self.assertNotIn(self.blueprint.id, recovered.node_results)
        self.assertNotIn(self.dependency_module.id, recovered.node_results)
        self.assertNotIn(self.module_api.id, recovered.node_results)
        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertNotIn(self.descendant.id, recovered.node_results)
        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.blueprint.id})
        diagnostic = recovered.recovery_diagnostics[self.blueprint.id]
        self.assertIn("backend/app/__init__.py", diagnostic)
        self.assertIn("required_files", diagnostic)
        event = self.container.traces.list_events(self.trace.trace_id)[-1]
        self.assertEqual(event["type"], "architecture_contract_recovery_scheduled")
        self.assertEqual(event["details"]["rerun_work_item_id"], self.blueprint.id)

    def test_preflight_replans_blueprint_for_all_unowned_required_files(self) -> None:
        state = RunState(
            plan=self.plan,
            node_results={item.id: NodeResult.completed(
                work_item_id=item.id, agent_id=item.agent_id, content=item.id
            ) for item in self.plan.work_items},
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace, self.integration.id, "work_item_needs_replan",
            details={
                "kind": "planner_validation",
                "error": "Architecture integration inputs failed deterministic validation: "
                         "ArchitectureBlueprint.required_file_not_owned: README.md, tests/test_tasks.py",
            },
        )
        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )
        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.blueprint.id})
        self.assertIn("README.md, tests/test_tasks.py", recovered.recovery_diagnostics[self.blueprint.id])
        self.assertIn(self.unrelated.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)

    def test_invalidates_module_design_with_stale_blueprint_parent(self) -> None:
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.module_api.id,
            artifact_key="architecture",
            slot=self.module_api.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "inventory-domain-design-v1",
                    "depth": 1,
                    "parent_design_id": "stale-blueprint-v0",
                    "module_id": "api",
                    "responsibilities": ["提供 HTTP 访问边界"],
                    "provided_interfaces": [],
                    "consumed_interfaces": [],
                    "entities": [],
                    "depends_on_modules": ["domain"],
                    "acceptance_criteria": [],
                    "requirement_ids": [],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 模块设计 inventory-domain-design-v1 "
                    "未引用当前总体蓝图"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.module_api.id})
        self.assertNotIn(self.module_api.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertIn(self.blueprint.id, recovered.node_results)
        self.assertIn("parent_design_id", recovered.recovery_diagnostics[self.module_api.id])

    def test_invalidates_implementation_design_with_stale_module_parent(self) -> None:
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.module_api.id,
            artifact_key="architecture",
            slot=self.module_api.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "inventory-api-module-v2",
                    "depth": 1,
                    "parent_design_id": "blueprint-v1",
                    "module_id": "api",
                    "responsibilities": ["提供 HTTP 访问边界"],
                    "provided_interfaces": [],
                    "consumed_interfaces": [],
                    "entities": [],
                    "depends_on_modules": ["domain"],
                    "acceptance_criteria": [],
                    "requirement_ids": [],
                }
            ),
        )
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.owner.id,
            artifact_key="architecture",
            slot=self.owner.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "inventory-api-implementation-v1",
                    "depth": 2,
                    "parent_design_id": "stale-module-v0",
                    "module_id": "api",
                    "provided_interfaces": [],
                    "consumed_interfaces": [],
                    "implementation_units": [
                        {
                            "unit_id": "api-service",
                            "layer": "application",
                            "objective": "提供库存 HTTP 服务",
                            "allowed_paths": ["backend/app/**"],
                            "owned_files": ["backend/app/api.py"],
                            "required_paths": ["backend/app/api.py"],
                        }
                    ],
                    "required_test_types": [],
                    "requirement_ids": [],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 实现设计 inventory-api-implementation-v1 "
                    "未引用已存在的模块设计"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.owner.id})
        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertIn("parent_design_id", recovered.recovery_diagnostics[self.owner.id])
        self.assertIn("inventory-api-module-v2", recovered.recovery_diagnostics[self.owner.id])

    def test_invalidates_owner_of_required_path_contract_violation(self) -> None:
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.owner.id,
            artifact_key="architecture",
            slot=self.owner.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "implementation-runtime-v1",
                    "depth": 2,
                    "parent_design_id": "module-runtime-v1",
                    "module_id": "runtime",
                    "provided_interfaces": [],
                    "consumed_interfaces": [],
                    "implementation_units": [
                        {
                            "unit_id": "runtime-service",
                            "layer": "runtime",
                            "objective": "组装服务",
                            "allowed_paths": ["inventory_service/runtime/**"],
                            "owned_files": ["inventory_service/runtime/app.py"],
                            "required_paths": ["inventory_service/domain/models.py"],
                        }
                    ],
                    "required_test_types": [],
                    "requirement_ids": [],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 实现单元 runtime-service 的 "
                    "required_paths 必须属于 owned_files: "
                    "inventory_service/domain/models.py"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        self.assertNotIn(self.descendant.id, recovered.node_results)
        self.assertIn(self.unrelated.id, recovered.node_results)
        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.owner.id})
        self.assertIn(
            "inventory_service/domain/models.py",
            recovered.recovery_diagnostics[self.owner.id],
        )

    def test_invalidates_owner_of_unknown_implementation_dependency(self) -> None:
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.dependency_module.id,
            artifact_key="architecture",
            slot=self.dependency_module.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "module-domain-v1",
                    "depth": 1,
                    "parent_design_id": "blueprint-v1",
                    "module_id": "domain",
                    "responsibilities": ["维护库存"],
                    "provided_interfaces": [{
                        "interface_id": "domain.inventory.query",
                        "direction": "provided",
                        "summary": "读取库存",
                    }],
                    "consumed_interfaces": [],
                    "entities": ["Stock"],
                    "depends_on_modules": [],
                    "acceptance_criteria": [],
                    "requirement_ids": [],
                }
            ),
        )
        self.container.artifact_repository.write_staged(
            trace_id=self.trace.trace_id,
            work_item_id=self.owner.id,
            artifact_key="architecture",
            slot=self.owner.slot or "",
            content=json.dumps(
                {
                    "schema_version": 1,
                    "design_id": "implementation-api-v1",
                    "depth": 2,
                    "parent_design_id": "module-api-v1",
                    "module_id": "api",
                    "provided_interfaces": [],
                    "consumed_interfaces": [],
                    "implementation_units": [{
                        "unit_id": "api-handler",
                        "layer": "application",
                        "objective": "提供库存 HTTP API",
                        "allowed_paths": ["backend/app/**"],
                        "owned_files": ["backend/app/api.py"],
                        "depends_on": ["domain"],
                    }],
                    "required_test_types": [],
                    "requirement_ids": [],
                }
            ),
        )
        state = RunState(
            plan=self.plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id, agent_id=item.agent_id, content=item.id
                )
                for item in self.plan.work_items
            },
            artifacts={item.output_key: item.id for item in self.plan.work_items},
        )
        self.container.traces.record_event(
            self.trace,
            self.integration.id,
            "work_item_failed",
            details={
                "error": (
                    "architecture_contract_missing: 实现单元 api-handler "
                    "依赖不存在的实现单元: domain"
                )
            },
        )

        recovered = _prepare_architecture_contract_recovery(
            self.container, self.trace.trace_id, self.plan, state
        )

        self.assertEqual(recovered.forced_rerun_work_item_ids, {self.owner.id})
        self.assertNotIn(self.owner.id, recovered.node_results)
        self.assertNotIn(self.integration.id, recovered.node_results)
        diagnostic = recovered.recovery_diagnostics[self.owner.id]
        self.assertIn("api-handler", diagnostic)
        self.assertIn("domain.inventory.query", diagnostic)
        self.assertIn("consumed_interfaces", diagnostic)
        self.alive = False


class FakeContext:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process

    def Process(self, *, target, args, name):  # noqa: N802 - multiprocessing API
        return self.process


class RunCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        Project.create_at(self.directory.name, name="demo")
        self.trace = TraceStore(self.directory.name).start_trace("测试 Worker")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_cancel_persists_terminal_state_and_is_idempotent(self) -> None:
        coordinator = RunCoordinator()
        trace_id = self.trace.trace_id

        first = coordinator.cancel(self.directory.name, trace_id, reason="测试停止")
        self.assertEqual(first, {
            "trace_id": trace_id,
            "status": "cancelled",
            "worker_process_state": "cancelled",
        })
        trace = TraceStore(self.directory.name).load_trace(trace_id)
        self.assertEqual(trace["status"], "cancelled")
        store = WorkerProgressStore(self.directory.name)
        self.assertTrue(store.cancel_requested(trace_id))

        second = coordinator.cancel(self.directory.name, trace_id, reason="重复停止")
        self.assertEqual(second["status"], "cancelled")
        events = TraceStore(self.directory.name).list_events(trace_id)
        self.assertEqual(
            [event["type"] for event in events].count("cancel_requested"),
            1,
        )
        coordinator.shutdown()

    def test_worker_deadline_tracks_meaningful_progress_not_process_age(self) -> None:
        started = datetime.now(timezone.utc)
        previous = None
        refreshed_at = started
        progress = {"run": {"last_meaningful_at": started.isoformat()}}
        previous, refreshed_at = _renew_worker_deadline(
            progress, monitor_started_at=started,
            previous_marker=previous, last_progress_at=refreshed_at,
            now=started + timedelta(seconds=1),
        )
        self.assertEqual(refreshed_at, started)
        # Heartbeats and raw transport chunks must not mask a true stall.
        progress["run"]["heartbeat_at"] = (started + timedelta(seconds=20)).isoformat()
        progress["run"]["last_event_at"] = progress["run"]["heartbeat_at"]
        previous, refreshed_at = _renew_worker_deadline(
            progress, monitor_started_at=started,
            previous_marker=previous, last_progress_at=refreshed_at,
            now=started + timedelta(seconds=50),
        )
        self.assertEqual(refreshed_at, started)
        # A new WorkItem/tool checkpoint renews the budget even after a long
        # healthy run in the same process.
        progress["run"]["last_meaningful_at"] = (
            started + timedelta(seconds=60)
        ).isoformat()
        previous, refreshed_at = _renew_worker_deadline(
            progress, monitor_started_at=started,
            previous_marker=previous, last_progress_at=refreshed_at,
            now=started + timedelta(seconds=61),
        )
        self.assertEqual(refreshed_at, started + timedelta(seconds=60))
        self.assertEqual(previous, progress["run"]["last_meaningful_at"])

    def test_worker_deadline_ignores_previous_worker_snapshot(self) -> None:
        started = datetime.now(timezone.utc)
        stale = {"run": {"last_meaningful_at": (
            started - timedelta(seconds=5)
        ).isoformat()}}
        marker, refreshed_at = _renew_worker_deadline(
            stale, monitor_started_at=started,
            previous_marker=None, last_progress_at=started,
            now=started + timedelta(seconds=100),
        )
        self.assertIsNone(marker)
        self.assertEqual(refreshed_at, started)

    def test_worker_timeout_terminates_process_and_finishes_trace(self) -> None:
        process = FakeProcess(alive=True, exitcode=None)
        coordinator = RunCoordinator(worker_timeout_seconds=0.01)
        progress_store = WorkerProgressStore(self.directory.name)
        progress_store.start_run(self.trace.trace_id)
        progress_store.record_work_item_activity(
            self.trace.trace_id,
            "wi-contract",
            "architecture_contract_agent",
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event_type="llm_chunk_batch",
            signal=ProgressSignalKind.TRANSPORT,
            llm={"state": "streaming", "call_id": "call-contract", "terminal_at": None},
        )
        progress_store.start_batch(
            self.trace.trace_id,
            "batch-w0-contract",
            wave_index=0,
            work_item_ids=("wi-contract",),
        )

        with patch(
            "app.application.runs.multiprocessing.get_context",
            return_value=FakeContext(process),
        ):
            result = coordinator._run_in_worker(self.directory.name, self.trace.trace_id)

        self.assertEqual(result, "failed")
        self.assertTrue(process.started)
        self.assertTrue(process.terminated)
        trace = TraceStore(self.directory.name).load_trace(self.trace.trace_id)
        self.assertEqual(trace["status"], "failed")
        progress = WorkerProgressStore(self.directory.name).read(self.trace.trace_id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["run"]["lifecycle"], "terminal")
        self.assertEqual(progress["run"]["outcome"], "failed")
        self.assertIsNotNone(progress["run"]["terminal_at"])
        self.assertEqual(progress["work_items"]["wi-contract"]["outcome"], "failed")
        self.assertEqual(progress["work_items"]["wi-contract"]["llm"]["state"], "failed")
        retry_records = RetryLedger(self.directory.name, self.trace.trace_id).records()
        self.assertEqual(len(retry_records), 1)
        self.assertEqual(retry_records[0].scope, RetryScope.BATCH)
        self.assertEqual(retry_records[0].action, RecoveryAction.RETRY_BATCH)
        self.assertEqual(retry_records[0].root_work_item_id, "wi-contract")
        events = TraceStore(self.directory.name).list_events(self.trace.trace_id)
        self.assertIn("work_item_failed", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "worker_timed_out")
        coordinator.shutdown()

    def test_active_repair_resume_consumes_monitor_retry_ledger(self) -> None:
        repair_plan = ExecutionPlan(
            id="worker-repair-1",
            goal="恢复修复批次",
            trace=self.trace,
            work_items=(self.plan_item("repair-root"), self.plan_item("repair-sibling")),
        )
        traces = TraceStore(self.directory.name)
        traces.record_plan(repair_plan)
        repair_state = RunState(
            plan=repair_plan,
            node_results={
                item.id: NodeResult.completed(
                    work_item_id=item.id,
                    agent_id=item.agent_id,
                    content=item.id,
                )
                for item in repair_plan.work_items
            },
            artifacts={item.output_key: item.id for item in repair_plan.work_items},
        )
        traces.record_checkpoint(repair_plan.trace, repair_state.as_checkpoint())
        RetryLedger(self.directory.name, self.trace.trace_id).append(
            RetryRecord(
                scope=RetryScope.BATCH,
                subject_id="batch-repair-1",
                attempt=1,
                max_attempts=2,
                action=RecoveryAction.RETRY_BATCH,
                failure=FailureSignal(FailureKind.PROVIDER_STALL, "Worker stalled"),
                root_work_item_id="repair-root",
                interrupted_work_item_ids=("repair-sibling",),
            )
        )

        plan, resumed = _load_delivery_resume(
            build_container(self.directory.name), self.trace.trace_id
        )

        self.assertEqual(plan.id, "worker-repair-1")
        self.assertEqual(resumed.node_results, {})
        self.assertEqual(
            set(resumed.retry_recovery_contexts),
            {"repair-root", "repair-sibling"},
        )
        self.assertIn(
            "retry_recovery_applied",
            [event["type"] for event in traces.list_events(self.trace.trace_id)],
        )

    def plan_item(self, item_id: str) -> WorkItem:
        return WorkItem(
            id=item_id,
            agent_id="requirement_agent",
            objective=item_id,
            output_key=item_id,
        )

    def test_worker_uses_persisted_trace_status_after_process_exit(self) -> None:
        process = FakeProcess(alive=False, exitcode=0)
        coordinator = RunCoordinator()

        TraceStore(self.directory.name).finish_trace(self.trace, "completed")
        with patch(
            "app.application.runs.multiprocessing.get_context",
            return_value=FakeContext(process),
        ):
            result = coordinator._run_in_worker(self.directory.name, self.trace.trace_id)

        self.assertEqual(result, "completed")
        self.assertTrue(process.started)
        coordinator.shutdown()

    def test_submit_rejects_duplicate_active_trace(self) -> None:
        coordinator = RunCoordinator()
        trace_id = self.trace.trace_id
        pending = Future()
        coordinator._futures[trace_id] = pending
        plan = SimpleNamespace(trace=SimpleNamespace(trace_id=trace_id))
        container = SimpleNamespace(project_path=self.directory.name, traces=SimpleNamespace())

        with self.assertRaises(PlannerFailure):
            coordinator.submit(container, plan)

        coordinator.shutdown()

    def test_shutdown_terminates_active_workers(self) -> None:
        process = FakeProcess(alive=True, exitcode=None)
        coordinator = RunCoordinator()
        coordinator._processes[self.trace.trace_id] = process

        coordinator.shutdown()

        self.assertTrue(process.terminated)

    def test_spawned_worker_rebuilds_runtime_and_persists_failure(self) -> None:
        container = build_container(self.directory.name)
        plan = ExecutionPlan(
            id="worker-smoke-plan",
            goal="验证隔离 Worker",
            trace=self.trace,
            work_items=(
                WorkItem(
                    id="unknown",
                    agent_id="missing_agent",
                    objective="触发结构化失败",
                    output_key="unknown_output",
                ),
            ),
        )
        container.traces.record_plan(plan)
        coordinator = RunCoordinator(worker_timeout_seconds=30)
        coordinator.submit(container, plan)

        deadline = time.monotonic() + 15
        status = "running"
        while time.monotonic() < deadline:
            status = coordinator.status(plan.trace.trace_id) or "missing"
            if status != "running":
                break
            time.sleep(0.1)

        self.assertEqual(status, "failed")
        trace = TraceStore(self.directory.name).load_trace(plan.trace.trace_id)
        self.assertEqual(trace["status"], "failed")
        event_types = [
            event["type"]
            for event in TraceStore(self.directory.name).list_events(plan.trace.trace_id)
        ]
        self.assertIn("worker_started", event_types)
        coordinator.shutdown()

    def test_stall_detection_checks_each_parallel_work_item(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        monitor_started_at = datetime.now(timezone.utc) - timedelta(seconds=500)
        progress = {
            "schema_version": 3,
            "trace_id": self.trace.trace_id,
            "run": {"lifecycle": "running", "last_event_at": datetime.now(timezone.utc).isoformat()},
            "batches": {},
            "work_items": {
                "wi-done": {
                    "work_item_id": "wi-done",
                    "lifecycle": "terminal",
                    "activity": "worker",
                    "outcome": "completed",
                    "last_event_at": datetime.now(timezone.utc).isoformat(),
                },
                "wi-stalled": {
                    "work_item_id": "wi-stalled",
                    "lifecycle": "running",
                    "activity": "llm",
                    "event_type": "llm_chunk_batch",
                    "last_event_at": old,
                    "last_meaningful_at": old,
                    "clocks": {"transport_at": old},
                    "llm": {"state": "streaming"},
                },
            },
        }

        observations = _stall_observations(
            progress, monitor_started_at=monitor_started_at
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].work_item_id, "wi-stalled")
        self.assertEqual(observations[0].kind, "provider_transport_stall")

    def test_streaming_llm_with_recent_transport_is_not_semantic_stall(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        progress = {
            "schema_version": 3,
            "trace_id": self.trace.trace_id,
            "run": {"lifecycle": "running"},
            "batches": {},
            "work_items": {
                "wi-streaming": {
                    "work_item_id": "wi-streaming", "lifecycle": "running",
                    "activity": "llm", "event_type": "llm_chunk_batch",
                    "last_event_at": recent, "last_meaningful_at": old,
                    "clocks": {"transport_at": recent},
                    "llm": {"state": "streaming"},
                }
            },
        }
        self.assertEqual(
            _stall_observations(progress, monitor_started_at=datetime.now(timezone.utc) - timedelta(seconds=500)),
            (),
        )

    def test_stall_detection_ignores_previous_worker_snapshot(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        progress = {
            "schema_version": 3,
            "trace_id": self.trace.trace_id,
            "run": {"lifecycle": "running", "last_event_at": old},
            "batches": {},
            "work_items": {
                "wi-old": {
                    "work_item_id": "wi-old",
                    "lifecycle": "running",
                    "activity": "llm",
                    "event_type": "llm_chunk_batch",
                    "last_event_at": old,
                    "last_meaningful_at": old,
                    "clocks": {"transport_at": old},
                    "llm": {"state": "streaming"},
                }
            },
        }

        observations = _stall_observations(
            progress, monitor_started_at=datetime.now(timezone.utc)
        )

        self.assertEqual(observations, ())


if __name__ == "__main__":
    unittest.main()
