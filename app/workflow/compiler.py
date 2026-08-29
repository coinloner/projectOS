"""将带系统授权的 WorkflowTemplate 编译为一次真实 ExecutionPlan。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import re

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.workflow.template import WorkflowTemplate
from app.domain.architecture.implementation_contract import ImplementationContract, ImplementationUnit
from app.orchestration.delivery_contract import DeliveryContract


class TemplateCompiler:
    """展开受控模板，不接受来自 Planner 的权限字段。"""

    def compile(
        self,
        template: WorkflowTemplate,
        *,
        goal: str,
        plan_id: str,
        trace: TraceContext,
        agent_output_keys: Mapping[str, str] | None = None,
    ) -> ExecutionPlan:
        if not template.has_controlled_execution:
            raise ValueError(
                f"模板 '{template.id}' 是普通模板，应由 PlanValidator 的动态路径处理"
            )
        contracts = agent_output_keys or {}
        unknown_agents = sorted(
            {node.agent_id for node in template.nodes} - set(contracts)
        )
        if unknown_agents:
            raise ValueError(
                f"受控模板引用未注册 Agent: {', '.join(unknown_agents)}"
            )

        item_id_by_blueprint = {
            node.id: f"wi-{index:02d}-{node.id}"
            for index, node in enumerate(template.nodes, 1)
        }
        staged_refs = {
            node.id: ArtifactRef.staged(
                artifact_key=self._artifact_key(node),
                trace_id=trace.trace_id,
                work_item_id=item_id_by_blueprint[node.id],
                slot=node.output_slot or "",
            )
            for node in template.nodes
            if node.execution_mode is ExecutionMode.PARTITIONED
        }

        work_items: list[WorkItem] = []
        for node in template.nodes:
            item_id = item_id_by_blueprint[node.id]
            dependencies = tuple(
                WorkItemDependency(
                    work_item_id=item_id_by_blueprint[predecessor],
                    source=DependencySource.TEMPLATE,
                    rule_id=f"template:{template.id}:{predecessor}->{node.id}",
                )
                for predecessor in node.depends_on
            )
            input_refs = self._input_refs(
                node=node,
                trace=trace,
                item_id_by_blueprint=item_id_by_blueprint,
                staged_refs=staged_refs,
            )
            if node.candidate_from is not None and node.candidate_from not in item_id_by_blueprint:
                raise ValueError(
                    f"Blueprint '{node.id}' 的 candidate_from 引用了未知节点"
                )
            candidate_from = (
                item_id_by_blueprint[node.candidate_from]
                if node.candidate_from is not None
                else None
            )
            work_items.append(
                WorkItem(
                    id=item_id,
                    agent_id=node.agent_id,
                    objective=node.objective,
                    output_key=node.output_key,
                    artifact_key=self._artifact_key(node),
                    dependencies=dependencies,
                    acceptance_criteria=node.acceptance_criteria,
                    constraints=node.constraints,
                    non_goals=node.non_goals,
                    policy_id=node.policy_id,
                    execution_mode=node.execution_mode,
                    input_refs=input_refs,
                    output_slot=node.output_slot,
                    publish_target=node.publish_target,
                    candidate_from_work_item_id=candidate_from,
                    implementation_unit_id=node.implementation_unit_id,
                    allowed_paths=node.allowed_paths,
                    forbidden_paths=node.forbidden_paths,
                    required_paths=node.required_paths,
                    owned_files=node.owned_files,
                    policy_refs=node.policy_refs,
                    skill_refs=node.skill_refs,
                )
            )

        plan = ExecutionPlan(
            id=plan_id,
            goal=goal,
            work_items=tuple(work_items),
            template_id=template.id,
            trace=trace,
        )
        if template.id == "project_delivery" and any(
            getattr(item, "implementation_unit_id", None) == "project-documents"
            for item in plan.work_items
        ):
            DeliveryContract.project_delivery().validate_plan(plan.work_items)
        return plan

    @staticmethod
    def _artifact_key(node) -> str:
        return node.artifact_key or node.publish_target or node.output_key

    def _input_refs(
        self,
        *,
        node,
        trace: TraceContext,
        item_id_by_blueprint: Mapping[str, str],
        staged_refs: Mapping[str, ArtifactRef],
    ) -> tuple[ArtifactRef, ...]:
        refs = [ArtifactRef.published(artifact_key) for artifact_key in node.input_artifacts]
        for source_id in node.input_from:
            if source_id not in item_id_by_blueprint:
                raise ValueError(
                    f"Blueprint '{node.id}' 的 input_from 引用了未知节点 '{source_id}'"
                )
            source_ref = staged_refs.get(source_id)
            if source_ref is None:
                raise ValueError(
                    f"Blueprint '{node.id}' 只能从 PARTITIONED 节点读取 staged output: '{source_id}'"
                )
            refs.append(source_ref)
        return tuple(refs)


class ImplementationContractCompiler:
    """将 Architecture 的实现合同编译为并行 CodeAgent WorkItem。"""

    def compile(
        self,
        contract: ImplementationContract,
        *,
        goal: str,
        plan_id: str,
        trace: TraceContext,
    ) -> ExecutionPlan:
        # Keep one complete file as the smallest CodeAgent delivery unit.  A
        # model can reliably satisfy a single-file contract; assigning several
        # service files to one prompt made partial ChangeSets common and left
        # the wave barrier blocked.  Expand multi-file units deterministically
        # while preserving the architecture-declared wave and dependencies.
        # The project-delivery template already owns ``review.md`` through its
        # dedicated review_agent node.  Architecture models sometimes repeat
        # that artifact as an implementation unit, which would incorrectly
        # schedule it as a CodeAgent file task (and can stall waiting for code
        # staging).  Keep implementation compilation focused on code, tests,
        # and operational files; the canonical review node remains in the
        # template graph.
        implementation_units = tuple(
            unit for unit in contract.units
            if not self._is_review_artifact_unit(unit)
        )
        units = self._split_file_units(implementation_units)
        ids = {unit.unit_id: f"wi-code-{unit.unit_id}" for unit in units}
        inferred_waves: dict[str, int] = {}
        units_by_id = {unit.unit_id: unit for unit in units}
        interfaces_by_id = {interface.interface_id: interface for interface in contract.interfaces}
        original_children: dict[str, tuple[str, ...]] = {}
        for original in contract.units:
            matching = tuple(
                unit.unit_id
                for unit in units
                if unit.unit_id == original.unit_id
                or unit.unit_id.startswith(original.unit_id + "-")
            )
            original_children[original.unit_id] = matching or (original.unit_id,)

        def origin(unit_id: str) -> str:
            for original in contract.units:
                if unit_id == original.unit_id or unit_id.startswith(original.unit_id + "-"):
                    return original.unit_id
            return unit_id

        def interface_dependencies(unit: ImplementationUnit) -> tuple[str, ...]:
            """Infer owner dependencies from consumed interfaces.

            Architecture may still declare explicit ``depends_on``; this adds
            only missing edges and keeps the original plan shape otherwise.
            """
            owners: list[str] = []
            for interface_id in unit.consumes_interfaces:
                interface = interfaces_by_id.get(interface_id)
                if interface is not None and interface.owner_unit != origin(unit.unit_id):
                    owners.extend(original_children.get(interface.owner_unit, (interface.owner_unit,)))
            return tuple(dict.fromkeys(owners))

        def wave_for(unit_id: str, visiting: set[str] | None = None) -> int:
            if unit_id in inferred_waves:
                return inferred_waves[unit_id]
            visiting = visiting or set()
            if unit_id in visiting:
                raise ValueError("Implementation Contract 存在循环 Wave 依赖")
            visiting.add(unit_id)
            unit = units_by_id[unit_id]
            declared_dependencies = tuple(dict.fromkeys((*unit.depends_on, *interface_dependencies(unit))))
            # ``wave`` is an architectural hint, not permission to violate a
            # dependency edge.  LLM-produced contracts occasionally assign a
            # consumer to an earlier wave than the provider it consumes (for
            # example a frontend client consuming the HTTP API).  Trusting the
            # hint verbatim lets the later wave barrier add a reverse edge and
            # creates an apparent cycle.  Compute the effective wave as the
            # maximum of the declared hint and every dependency's wave + 1.
            dependency_wave = max(
                (wave_for(dep, visiting) + 1 for dep in declared_dependencies),
                default=0,
            )
            value = max(unit.wave if unit.wave is not None else 0, dependency_wave)
            visiting.remove(unit_id)
            inferred_waves[unit_id] = value
            return value

        for unit in units:
            wave_for(unit.unit_id)
        items: list[WorkItem] = []
        for unit in units:
            slot = unit.output_slot or self._infer_slot(
                unit.allowed_paths, layer=unit.layer, unit_id=unit.unit_id
            )
            allowed_paths = self._normalize_paths(unit.allowed_paths, slot)
            forbidden_paths = self._normalize_paths(unit.forbidden_paths, slot)
            required_paths = self._normalize_paths(unit.required_paths, slot)
            # 项目文档单元位于代码集成之前，只能要求它自己负责的规划文档。
            # environment/implementation/tests/review 是后续节点的证据产物，
            # 若在此处作为 required_paths 会让集成在逻辑上必然失败。
            if unit.unit_id == "project-documents":
                required_paths = tuple(
                    path for path in required_paths
                    if path in {
                        "requirement.md",
                        "architecture.md",
                        "architecture_contract.md",
                        "tasks.md",
                    }
                )
            # The trusted FastAPI profiles launch ``app.main:app``.  Make the
            # executable entrypoint part of the API unit contract even when a
            # model omits it from the generated path mapping.
            layer_name = unit.layer.strip().lower()
            backend_oriented = (
                unit.unit_id in {"backend-api", "backend-interfaces"}
                or any("backend/" in path.removeprefix("workspace/") for path in allowed_paths)
            )
            if layer_name in {"api", "interface", "interfaces"} and backend_oriented:
                backend_entry = contract.entrypoints.backend_file or "backend/app/main.py"
                # The entrypoint belongs to exactly one file-level WorkItem.
                # Do not inject it into every API child: doing so turns a
                # routes/schema task into an impossible multi-file contract.
                if backend_entry in unit.owned_files:
                    if backend_entry not in allowed_paths:
                        allowed_paths = (*allowed_paths, backend_entry)
                    if backend_entry not in required_paths:
                        required_paths = (*required_paths, backend_entry)
            if layer_name in {"frontend", "web", "interface"} or "frontend" in unit.unit_id.lower():
                frontend_entry = contract.entrypoints.frontend_file
                if frontend_entry and frontend_entry in unit.owned_files:
                    if frontend_entry not in allowed_paths:
                        allowed_paths = (*allowed_paths, frontend_entry)
                    if frontend_entry not in required_paths:
                        required_paths = (*required_paths, frontend_entry)
            declared_dependencies = tuple(dict.fromkeys((*unit.depends_on, *interface_dependencies(unit))))
            dependencies = tuple(
                WorkItemDependency(ids[dep], DependencySource.SYSTEM, "implementation-contract")
                for dep in declared_dependencies
            )
            # A Wave is a barrier, not just display metadata.  Every unit in a
            # later Wave waits for the previous Waves to finish, while units in
            # the same Wave remain independently schedulable.
            barrier_dependencies = tuple(
                WorkItemDependency(ids[previous.unit_id], DependencySource.SYSTEM, "implementation-wave-barrier")
                for previous in units
                if inferred_waves[previous.unit_id] < inferred_waves[unit.unit_id]
                and previous.unit_id not in unit.depends_on
            )
            merged_dependencies: list[WorkItemDependency] = []
            seen_dependency_ids: set[str] = set()
            for dependency in (*dependencies, *barrier_dependencies):
                if dependency.work_item_id in seen_dependency_ids:
                    continue
                seen_dependency_ids.add(dependency.work_item_id)
                merged_dependencies.append(dependency)
            dependencies = tuple(merged_dependencies)
            dependency_refs = tuple(
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id=trace.trace_id,
                    work_item_id=ids[dep],
                    slot=(units_by_id[dep].output_slot or self._infer_slot(
                        units_by_id[dep].allowed_paths,
                        layer=units_by_id[dep].layer,
                        unit_id=units_by_id[dep].unit_id,
                    )),
                )
                for dep in declared_dependencies
            )
            items.append(
                WorkItem(
                    id=ids[unit.unit_id],
                    agent_id="code_agent",
                    objective=unit.objective,
                    output_key=unit.output_key or f"implementation_{unit.unit_id}",
                    artifact_key="implementation",
                    dependencies=dependencies,
                    acceptance_criteria=unit.acceptance_criteria,
                    constraints=unit.constraints,
                    non_goals=unit.non_goals,
                    execution_mode=ExecutionMode.PARTITIONED,
                    input_refs=tuple(ArtifactRef.published(ref) for ref in unit.input_refs) + dependency_refs,
                    output_slot=slot,
                    implementation_unit_id=unit.unit_id,
                    allowed_paths=allowed_paths,
                    forbidden_paths=forbidden_paths,
                    required_paths=required_paths,
                    policy_refs=unit.policy_refs,
                    skill_refs=unit.skill_refs,
                    requirement_ids=unit.requirement_ids,
                    wave=inferred_waves[unit.unit_id],
                    owned_files=unit.owned_files,
                    delivery_contract={
                        "entrypoints": contract.entrypoints.as_dict(),
                        "required_files": list(contract.required_files),
                        "interfaces": [
                            interfaces_by_id[interface_id].as_dict()
                            for interface_id in (*unit.provides_interfaces, *unit.consumes_interfaces)
                            if interface_id in interfaces_by_id
                        ],
                        "provided_symbols": list(unit.provided_symbols),
                        "required_symbols": list(unit.required_symbols),
                        "provides_interfaces": list(unit.provides_interfaces),
                        "consumes_interfaces": list(unit.consumes_interfaces),
                    },
                )
            )
        return ExecutionPlan(
            id=plan_id,
            goal=goal,
            work_items=tuple(items),
            template_id="implementation-contract",
            trace=trace,
        )

    @staticmethod
    def _is_review_artifact_unit(unit: ImplementationUnit) -> bool:
        if unit.unit_id == "project-documents":
            return False
        unit_id = unit.unit_id.strip().lower().replace("_", "-")
        if unit_id in {"review", "u-review", "project-review"}:
            return True
        paths = {
            path.removeprefix("workspace/").lstrip("/").strip().lower()
            for path in (*unit.owned_files, *unit.required_paths)
        }
        return "review.md" in paths

    @staticmethod
    def _split_file_units(units: tuple[ImplementationUnit, ...]) -> tuple[ImplementationUnit, ...]:
        """Expand a unit with multiple owned files into one unit per file.

        Dependencies are rewritten to all children of the referenced unit so
        a layer cannot start until the complete prerequisite layer is ready.
        Every CodeAgent unit must identify a concrete delivery file.  The
        planning-document unit is retained as a special non-code artifact for
        the legacy project-delivery contract; all other units fail early when
        ownership is omitted instead of silently falling back to a directory.
        """
        children: dict[str, tuple[str, ...]] = {}
        source_by_child: dict[str, ImplementationUnit] = {}
        expanded: list[ImplementationUnit] = []
        for unit in units:
            if not unit.owned_files and unit.unit_id != "project-documents":
                raise ValueError(
                    f"实现单元 '{unit.unit_id}' 必须声明一个或多个 concrete owned_files；"
                    "目录授权请使用 allowed_paths/allowed_roots，不能作为交付边界"
                )
            if len(unit.owned_files) <= 1:
                children[unit.unit_id] = (unit.unit_id,)
                expanded.append(unit)
                source_by_child[unit.unit_id] = unit
                continue
            ids: list[str] = []
            for index, path in enumerate(unit.owned_files):
                suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_.-")
                suffix = suffix or str(index + 1)
                child_id = f"{unit.unit_id}-{suffix or index + 1}"
                ids.append(child_id)
                required = tuple(value for value in unit.required_paths if value == path)
                expanded.append(
                    replace(
                        unit,
                        unit_id=child_id,
                        objective=ImplementationContractCompiler._file_objective(unit, path),
                        allowed_paths=(path,),
                        required_paths=required or (path,),
                        depends_on=(),
                        owned_files=(path,),
                        output_key=f"{unit.output_key or unit.unit_id}_{suffix}",
                        # ``output_slot`` is a physical partition (backend,
                        # frontend or root), not an artifact id.  Keep it
                        # stable across file children so path normalization and
                        # staging authorization remain identical to the parent
                        # unit; WorkItem/output_key already provide uniqueness.
                        output_slot=unit.output_slot,
                        # Public symbols are file-scoped.  A legacy multi-file
                        # unit has no unambiguous symbol-to-file mapping, so it
                        # must omit symbols (or be split by Architecture) rather
                        # than making every child claim every symbol.
                        provided_symbols=unit.provided_symbols if len(unit.owned_files) == 1 else (),
                    )
                )
                source_by_child[child_id] = unit
            children[unit.unit_id] = tuple(ids)

        rewritten: list[ImplementationUnit] = []
        for unit in expanded:
            # Child units carry no dependencies yet; map the original unit id
            # back to every generated child.  Existing single-file units map
            # to themselves.
            source = source_by_child[unit.unit_id]
            deps = tuple(child for dep in source.depends_on for child in children[dep])
            rewritten.append(replace(unit, depends_on=deps))
        return tuple(rewritten)

    @staticmethod
    def _file_objective(unit: ImplementationUnit, path: str) -> str:
        """将多文件单元收敛为单文件职责，避免组合根吞掉整个接口层。

        Architecture 的单元目标通常描述整个目录；拆成完整文件后继续复用该
        目标会让 CodeAgent 误以为自己仍需实现整个层。这里仅补充文件级职责，
        不改变架构声明的业务范围或授权路径。
        """
        normalized = path.removeprefix("workspace/").lstrip("/")
        basename = normalized.rsplit("/", 1)[-1]
        if basename == "main.py" and normalized.startswith("backend/"):
            role = (
                "只负责 FastAPI 组合根：创建 app、注册已存在的路由/异常处理、"
                "配置 lifespan 和健康入口；不得在此文件实现业务用例、数据库查询、"
                "Pydantic 业务 schema 或新的业务功能。"
            )
        elif "/interfaces/" in f"/{normalized}/" and basename in {"routes.py", "router.py"}:
            role = "只负责 HTTP 路由适配：调用 application 用例端口并映射请求/响应，不实现领域规则或数据库访问。"
        elif "/interfaces/" in f"/{normalized}/" and basename in {"schemas.py", "dto.py"}:
            role = "只负责 HTTP 输入输出 schema 和序列化校验，不实现业务决策、事务或数据库访问。"
        elif "/interfaces/" in f"/{normalized}/" and basename in {"dependencies.py", "auth.py"}:
            role = "只负责依赖注入和认证上下文适配，返回 application 所需端口，不实现业务用例。"
        elif "/interfaces/" in f"/{normalized}/" and basename in {"errors.py", "exceptions.py"}:
            role = "只负责异常到 HTTP 响应的映射，不新增业务错误或修改领域状态。"
        else:
            role = f"只负责完整文件 {normalized}，不得修改其他文件或承担同目录其他文件的职责。"
        return f"{role} 原架构目标：{unit.objective}"

    @staticmethod
    def _infer_slot(
        paths: tuple[str, ...], *, layer: str = "", unit_id: str = ""
    ) -> str:
        """推导物理分区，逻辑实现单元不再限于 backend/frontend。"""
        for path in paths:
            normalized = path.removeprefix("workspace/").lstrip("/")
            if normalized.startswith("backend/"):
                return "backend"
            if normalized.startswith("frontend/"):
                return "frontend"
        normalized_layer = layer.strip().lower()
        normalized_id = unit_id.strip().lower()
        if normalized_layer in {"tests", "test", "delivery", "scripts", "config"}:
            return "root"
        if any(
            path.removeprefix("workspace/").lstrip("/").startswith("app/")
            for path in paths
        ):
            return "backend"
        if any(token in normalized_layer or token in normalized_id for token in ("frontend", "web")):
            return "frontend"
        return "root"

    @staticmethod
    def _normalize_paths(paths: tuple[str, ...], slot: str) -> tuple[str, ...]:
        normalized: list[str] = []
        for path in paths:
            value = path.removeprefix("workspace/").lstrip("/")
            if slot == "backend" and value.startswith("app/"):
                value = f"backend/{value}"
            elif slot == "frontend" and value.startswith("app/"):
                value = f"frontend/{value}"
            normalized.append(value)
        return tuple(dict.fromkeys(normalized))
