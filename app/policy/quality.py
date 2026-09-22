"""节点输出的最小可量化质量规则。"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import fnmatch
from pathlib import Path
import re

import yaml

from app.artifact.repository import ArtifactRef
from app.workspace.git_repository import ChangeSet
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.domain.architecture.implementation_contract import ImplementationContract


@dataclass(frozen=True)
class QualityIssue:
    rule_id: str
    summary: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    policy_id: str
    issues: tuple[QualityIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class PolicyGuidance:
    """执行前提供给 Agent 的约束摘要；不直接决定放行。"""

    policy_id: str
    checklist: tuple[str, ...]
    references: tuple[str, ...] = ()

    def as_prompt(self) -> str:
        lines = [f"Policy 参考 ({self.policy_id})："]
        lines.extend(f"- {item}" for item in self.checklist)
        if self.references:
            lines.append("参考规则: " + ", ".join(self.references))
        return "\n".join(lines)


def _has_database_init_entrypoint(root: Path, workspace: Path) -> bool:
    """Return whether a project exposes a usable database migration entry.

    ProjectOS-generated Compose files may invoke ``migrate.py`` conditionally
    and projects commonly use Alembic's ``migrations/env.py`` plus version
    scripts instead of a standalone bootstrap script. Both forms are valid.
    """
    candidates = (root, workspace)
    for base in candidates:
        backend = base / "backend"
        operations = base / "operations"
        if any((backend / name).is_file() for name in ("migrate.py", "init_db.py")):
            return True
        if (operations / "migrate.sh").is_file():
            return True
        migrations = backend / "migrations"
        if (migrations / "env.py").is_file() and any(
            path.is_file() for path in (migrations / "versions").glob("*.py")
        ):
            return True
    return False


def _entrypoint_assembly_issue(
    workspace: Path, backend_file: str | None
) -> QualityIssue | None:
    """Check the minimal composition contract for generated Python servers.

    The generated stdlib runtime entrypoint loads an API module through one of
    ``create_application``/``create_app``/``handle``.  Importing the modules
    alone does not exercise that boundary, so a missing assembly function can
    otherwise pass the unit smoke suite and only fail when the server starts.
    This check is intentionally static: ProjectOS never executes untrusted
    project code outside the sandbox.
    """

    if not backend_file or not backend_file.endswith(".py"):
        return None
    relative = backend_file.removeprefix("workspace/").lstrip("/")
    entrypoint = workspace / relative
    if not entrypoint.is_file():
        return None
    try:
        source = entrypoint.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(entrypoint))
    except (OSError, SyntaxError):
        # Syntax validity is covered by the sandbox; do not duplicate that
        # diagnostic here or turn an unrelated parser failure into a second
        # quality finding.
        return None
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "_load_application" not in function_names:
        return None

    # Resolve the conventional sibling API module used by the generated
    # stdlib runtime.  If a custom runtime imports another module, its own
    # integration/behavior checks remain responsible for that contract.
    api_file = entrypoint.parent / "api.py"
    if not api_file.is_file():
        return QualityIssue(
            "runtime.application_assembly_missing",
            f"运行入口 {relative} 使用动态应用组装，但缺少相邻 api.py",
            (backend_file,),
        )
    try:
        api_tree = ast.parse(
            api_file.read_text(encoding="utf-8", errors="replace"),
            filename=str(api_file),
        )
    except (OSError, SyntaxError):
        return None
    api_functions = {
        node.name
        for node in ast.walk(api_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = {"create_application", "create_app", "handle"}
    if api_functions.isdisjoint(expected):
        return QualityIssue(
            "runtime.application_assembly_missing",
            (
                f"运行入口 {relative} 需要 api.py 提供 create_application、"
                "create_app 或 handle，但未发现可调用的应用组装函数"
            ),
            (backend_file, api_file.relative_to(workspace).as_posix()),
        )
    return None


class GitCodeIntegrationPolicy:
    """Git ChangeSet 版本的代码集成底线策略。

    该策略验证控制面事实和可执行入口：分区是否齐全、ChangeSet 是否存在、文件是否
    留在分区声明的目录中、合同要求的文件是否落盘，以及所有分区是否共享同一个
    baseline。业务代码的正确性仍由 TestAgent 和后续质量门负责。
    """

    policy_id = "code.git-integration.v1"

    def evaluate(
        self,
        outputs: tuple[tuple[ArtifactRef, ChangeSet], ...],
        *,
        missing_refs: tuple[ArtifactRef, ...] = (),
        common_baseline: str | None = None,
        is_ancestor: Callable[[str, str], bool] | None = None,
    ) -> QualityReport:
        issues: list[QualityIssue] = []
        if not outputs:
            issues.append(
                QualityIssue(
                    rule_id="code.changesets_required",
                    summary="代码集成至少需要一个 Git ChangeSet。",
                )
            )

        for ref in missing_refs:
            issues.append(
                QualityIssue(
                    rule_id="code.changeset_available",
                    summary=f"分区 {ref.slot} 没有可用的 Git ChangeSet。",
                    evidence_refs=(ref.ref_id,),
                )
            )

        baselines = {change.base_commit for _, change in outputs}
        compatible_baselines = (
            len(baselines) <= 1
            or (
                common_baseline is not None
                and is_ancestor is not None
                and all(is_ancestor(base, common_baseline) for base in baselines)
            )
        )
        if not compatible_baselines:
            issues.append(
                QualityIssue(
                    rule_id="code.common_baseline_required",
                    summary="所有代码分区必须从同一个 baseline 创建。",
                    evidence_refs=tuple(change.commit for _, change in outputs),
                )
            )

        seen_files: set[str] = set()
        for ref, change in outputs:
            if ref.layer != "staged" or ref.artifact_key != "implementation":
                issues.append(
                    QualityIssue(
                        rule_id="code.invalid_source_ref",
                        summary="Git 代码集成只能使用 implementation 暂存引用。",
                        evidence_refs=(ref.ref_id,),
                    )
                )
                continue
            if not change.changed_files:
                issues.append(
                    QualityIssue(
                        rule_id="code.files_required",
                        summary=f"分区 {ref.slot} 的 ChangeSet 没有文件变更。",
                        evidence_refs=(change.commit,),
                    )
                )
            workspace = Path(change.worktree_path) / "workspace"
            changed_relative = {path.removeprefix("workspace/") for path in change.changed_files}
            for required in change.required_paths:
                normalized = required.removeprefix("workspace/").lstrip("/")
                required_matches = (
                    list(workspace.glob(normalized))
                    if any(token in normalized for token in "*?[")
                    else [workspace / normalized]
                )
                # Contracts may require a directory (for example ``tests/``)
                # or a file/glob.  Treat the path kind explicitly instead of
                # requiring every declaration to be a file.
                # A required path may be a directory even when the contract
                # omits the trailing slash (the planner emits paths such as
                # ``backend/app/domain``).  Treat an existing directory as a
                # directory contract and require at least one changed file
                # beneath it.
                required_is_directory = normalized.endswith(("/", "/**", "/*")) or any(
                    path.is_dir() for path in required_matches
                )
                if required_is_directory:
                    present = any(path.is_dir() for path in required_matches)
                    changed = any(
                        candidate == normalized.rstrip("/").rstrip("*")
                        or candidate.startswith(
                            normalized.removesuffix("**").removesuffix("*").rstrip("/") + "/"
                        )
                        for candidate in changed_relative
                    )
                else:
                    present = any(path.is_file() for path in required_matches)
                    changed = any(
                        fnmatch.fnmatch(path, normalized)
                        or fnmatch.fnmatch(path, normalized.replace("**", "*"))
                        for path in changed_relative
                    )
                if not present:
                    issues.append(QualityIssue(
                        rule_id="code.required_path_missing",
                        summary=f"分区 {ref.slot} 缺少合同要求文件: {normalized}",
                        evidence_refs=(ref.ref_id, change.commit),
                    ))
                elif not changed:
                    issues.append(QualityIssue(
                        rule_id="code.required_path_not_changed",
                        summary=f"合同要求文件未包含在本次 ChangeSet: {normalized}",
                        evidence_refs=(ref.ref_id, change.commit),
                    ))
            # 旧版 backend/frontend 分区仍要求物理目录边界；root 或其他逻辑
            # 分区依靠独立 task worktree + Implementation Contract 路径授权。
            prefix = f"workspace/{ref.slot}/" if ref.slot in {"backend", "frontend"} else "workspace/"
            for path in change.changed_files:
                if not path.startswith(prefix):
                    issues.append(
                        QualityIssue(
                            rule_id="code.scope_boundary",
                            summary=f"文件 {path} 越过 {ref.slot} 分区边界。",
                            evidence_refs=(change.commit,),
                        )
                    )
                if path in seen_files:
                    issues.append(
                        QualityIssue(
                            rule_id="code.duplicate_path",
                            summary=f"多个代码分区声明了同一个文件: {path}。",
                            evidence_refs=(change.commit,),
                        )
                    )
                seen_files.add(path)
                if path.endswith(".py"):
                    source = Path(change.worktree_path) / path
                    try:
                        source_text = source.read_text(encoding="utf-8")
                        ast.parse(source_text)
                        if not source_text.strip():
                            raise ValueError("文件为空")
                    except (OSError, SyntaxError, ValueError) as error:
                        issues.append(QualityIssue(
                            rule_id="code.python_source_invalid",
                            summary=f"Python 文件无法通过基础检查: {path} ({error})",
                            evidence_refs=(change.commit,),
                        ))
            if ref.slot == "backend" and any(
                path.endswith("backend/app/main.py") for path in change.changed_files
            ):
                entrypoint = workspace / "backend/app/main.py"
                try:
                    tree = ast.parse(entrypoint.read_text(encoding="utf-8"))
                    assignments = {
                        node.targets[0].id
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Assign)
                        and node.targets
                        and isinstance(node.targets[0], ast.Name)
                    }
                    assignments.update(
                        node.target.id
                        for node in ast.walk(tree)
                        if isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                    )
                    if "app" not in assignments:
                        raise ValueError("未发现名为 app 的模块级应用对象")
                    if not any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "FastAPI"
                        for node in ast.walk(tree)
                    ):
                        raise ValueError("未发现 FastAPI() 应用构造")
                except (OSError, SyntaxError, ValueError) as error:
                    issues.append(QualityIssue(
                        rule_id="code.fastapi_entrypoint_invalid",
                        summary=f"backend/app/main.py 不是可识别的 FastAPI 入口: {error}",
                        evidence_refs=(change.commit,),
                    ))
        return QualityReport(policy_id=self.policy_id, issues=tuple(issues))


class ProjectRuntimePreflight:
    """生成项目的静态可运行性检查。

    该检查不执行 Docker 或数据库，只验证启动配置引用的文件确实存在。
    它在 CodeAgent 合并后、TestAgent 运行前执行，避免把明显的入口缺失
    留到最终 Review 才发现。
    """

    policy_id = "project.runtime-preflight.v1"

    def evaluate(self, project_path: str) -> QualityReport:
        root = Path(project_path).resolve()
        workspace = root / "workspace"
        issues: list[QualityIssue] = []
        try:
            from app.domain.architecture.implementation_contract import ProjectContractStore
            delivery_contract = ProjectContractStore(str(root)).load()
        except (FileNotFoundError, PermissionError, ValueError):
            delivery_contract = None
        if delivery_contract is not None:
            for required in delivery_contract.required_files:
                relative = required.removeprefix("workspace/").lstrip("/")
                if not (workspace / relative).is_file():
                    issues.append(QualityIssue(
                        "runtime.contract_required_file_missing",
                        f"Project Contract 要求文件不存在: {relative}",
                        (".projectos/architecture/project-contract.json",),
                    ))
            for label, relative in (
                ("backend", delivery_contract.entrypoints.backend_file),
                ("frontend", delivery_contract.entrypoints.frontend_file),
            ):
                if relative:
                    candidate = workspace / relative.removeprefix("workspace/")
                    if not candidate.is_file():
                        issues.append(QualityIssue(
                            "runtime.contract_entrypoint_missing",
                            f"Project Contract 声明的 {label} 入口不存在: {relative}",
                            (".projectos/architecture/project-contract.json",),
                        ))
            assembly_issue = _entrypoint_assembly_issue(
                workspace, delivery_contract.entrypoints.backend_file
            )
            if assembly_issue is not None:
                issues.append(assembly_issue)
        compose = next(
            (path for path in (workspace / "docker-compose.yml", root / "docker-compose.yml", workspace / "compose.yaml", root / "compose.yaml") if path.is_file()),
            None,
        )
        if compose is not None:
            text = compose.read_text(encoding="utf-8", errors="replace")
            if delivery_contract is not None and delivery_contract.entrypoints.backend_command:
                command = delivery_contract.entrypoints.backend_command
                # Compose accepts either a shell string or an argv list. A raw
                # substring check rejects the valid list form generated by our
                # launcher (``- python`` / ``- -m`` / ``- backend...``), so
                # compare the parsed backend command semantically.
                actual_command = ""
                try:
                    document = yaml.safe_load(text)
                    services = document.get("services", {}) if isinstance(document, dict) else {}
                    backend = services.get("backend", {}) if isinstance(services, dict) else {}
                    value = backend.get("command") if isinstance(backend, dict) else None
                    if isinstance(value, list):
                        actual_command = " ".join(str(part) for part in value)
                    elif isinstance(value, str):
                        actual_command = value
                except (OSError, TypeError, ValueError, yaml.YAMLError):
                    actual_command = ""
                if " ".join(command.split()) not in " ".join(actual_command.split()) and command not in text:
                    issues.append(QualityIssue(
                        "runtime.contract_entrypoint_command_mismatch",
                        f"Compose 未使用合同声明的后端启动命令: {command}",
                        (str(compose.relative_to(root)),),
                    ))
            for context in re.findall(r"^\s*context:\s*([^\s#]+)", text, re.MULTILINE):
                context_path = (compose.parent / context).resolve()
                if not (context_path / "Dockerfile").is_file():
                    issues.append(QualityIssue(
                        "runtime.dockerfile_missing",
                        f"Compose build context 缺少 Dockerfile: {context}",
                        (str(compose.relative_to(root)),),
                    ))
            if re.search(r"""(?:^|[\s;'\"])(?:migrate|init_db)\.py(?:[\s;'"]|$)""", text):
                optional_script = bool(re.search(
                    r"if\s+\[\s*-f\s+(?:/workspace/)?(?:migrate|init_db)\.py\s*\]",
                    text,
                ))
                if not _has_database_init_entrypoint(root, workspace) and not optional_script:
                    issues.append(QualityIssue(
                        "runtime.database_init_missing",
                        "Compose 启动命令引用数据库初始化，但项目中不存在迁移入口",
                        (str(compose.relative_to(root)),),
                    ))
        index = workspace / "frontend" / "index.html"
        if index.is_file():
            frontend = index.parent
            html = index.read_text(encoding="utf-8", errors="replace")
            refs = re.findall(r"(?:src|href)=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
            for ref in refs:
                if ref.startswith(("http://", "https://", "data:", "#", "mailto:")):
                    continue
                relative = ref.split("?", 1)[0].split("#", 1)[0].lstrip("/")
                if relative and not (frontend / relative).is_file():
                    issues.append(QualityIssue(
                        "runtime.frontend_reference_missing",
                        f"frontend/index.html 引用了不存在的文件: frontend/{relative}",
                        ("workspace/frontend/index.html",),
                    ))
        return QualityReport(self.policy_id, tuple(issues))


class ProjectQualityPolicy:
    """项目交付级质量底线。

    这是确定性结构检查，不试图判断业务正确性；它只阻止明显的单体堆积和
    不可启动交付。业务行为仍由测试证据与 ReviewAgent 判断。
    """

    policy_id = "project.quality.v1"

    def preflight(self, *, allowed_paths: tuple[str, ...] = (), required_paths: tuple[str, ...] = ()) -> PolicyGuidance:
        checklist = [
            "只修改 implementation contract 授权的路径。",
            "完成后逐项核对 acceptance criteria，并保留可验证证据。",
        ]
        if allowed_paths:
            checklist.append("允许路径: " + ", ".join(allowed_paths))
        if required_paths:
            checklist.append("必须产出: " + ", ".join(required_paths))
        return PolicyGuidance(self.policy_id, tuple(checklist), ("project.layer-boundary.v1",))

    def evaluate(self, project_path: str) -> QualityReport:
        root = Path(project_path).resolve()
        workspace = root / "workspace"
        application_id = None
        if (root / "runtime.yaml").is_file():
            try:
                from app.runtime.manifest import RuntimeManifest
                from app.runtime.application import ApplicationCatalog
                application_id = ApplicationCatalog.resolve(
                    str(root), RuntimeManifest.load(str(root)).application
                )
            except (FileNotFoundError, PermissionError, ValueError):
                application_id = None
        if not workspace.is_dir():
            return QualityReport(self.policy_id, ())
        files = [path for path in workspace.rglob("*") if path.is_file()]
        if not files:
            return QualityReport(self.policy_id, ())

        issues: list[QualityIssue] = []
        from app.domain.architecture.implementation_contract import ProjectContractStore
        contract_store = ProjectContractStore(str(root))
        contract: ImplementationContract | None = None
        if (root / "architecture.md").is_file():
            if not contract_store.exists():
                issues.append(QualityIssue(
                    rule_id="project.project_contract_required",
                    summary="项目存在架构或运行时交付物，但缺少 .projectos/architecture/project-contract.json",
                ))
            else:
                try:
                    contract = contract_store.load()
                except (ValueError, OSError) as error:
                    issues.append(QualityIssue(
                        rule_id="project.project_contract_invalid",
                        summary=f"Project Contract 无法校验: {error}",
                    ))
        if (root / "runtime.yaml").is_file():
            runtime_text = (root / "runtime.yaml").read_text(encoding="utf-8", errors="replace")
            dependencies_file = root / "requirements.in"
            fastapi_runtime = "fastapi" in runtime_text or "fastapi" in runtime_text.lower()
            if fastapi_runtime:
                if not dependencies_file.is_file():
                    issues.append(QualityIssue(
                        rule_id="project.runtime_dependencies_required",
                        summary="FastAPI runtime 缺少 requirements.in，无法确定 Uvicorn 和数据库依赖",
                    ))
                else:
                    dependencies = dependencies_file.read_text(encoding="utf-8", errors="replace").lower()
                    if "uvicorn" not in dependencies:
                        issues.append(QualityIssue(
                            rule_id="project.uvicorn_dependency_required",
                            summary="FastAPI 项目的 requirements.in 必须声明 uvicorn[standard]",
                        ))
                compose_candidates = [root / "docker-compose.yml", root / "compose.yaml", root / "workspace" / "compose.yaml"]
                compose_text = "\n".join(
                    path.read_text(encoding="utf-8", errors="replace")
                    for path in compose_candidates if path.is_file()
                )
                if compose_text and "uvicorn" not in compose_text:
                    issues.append(QualityIssue(
                        rule_id="project.uvicorn_command_required",
                        summary="FastAPI Compose 配置缺少 Uvicorn 启动命令",
                    ))
            for name in ("start.sh", "start.ps1", "start-managed.sh", "docker-compose.yml"):
                script = root / name
                if not script.is_file():
                    issues.append(
                        QualityIssue(
                            rule_id="project.startup_script_required",
                            summary=f"缺少标准一键启动脚本: {name}",
                        )
                    )
                else:
                    content = script.read_text(encoding="utf-8", errors="replace")
                    if name in {"start.sh", "start.ps1"} and "local_runtime" not in content:
                        issues.append(
                            QualityIssue(
                                rule_id="project.startup_script_boundary",
                                summary=f"启动脚本 {name} 未指向受信本地运行器",
                            )
                        )

        backend = workspace / "backend"
        backend_files = list(backend.rglob("*.py")) if backend.is_dir() else []
        if backend_files:
            app = backend / "app"
            categories = {
                "interface": any((app / name).exists() for name in ("interface", "api", "routers", "routes")),
                "application": any((app / name).exists() for name in ("services", "service", "use_cases", "application"))
                or any(path.name in {"service.py", "services.py", "use_cases.py"} for path in backend_files),
                "infrastructure": any((app / name).exists() for name in ("repositories", "repository", "infrastructure", "database"))
                or any(path.name in {"database.py", "repositories.py", "repository.py"} for path in backend_files),
                "domain": any((app / name).exists() for name in ("domain", "models", "schemas"))
                or any(path.name in {"models.py", "schemas.py"} for path in backend_files),
            }
            if application_id != "python-backend" and sum(categories.values()) < 4:
                missing = ", ".join(name for name, present in categories.items() if not present)
                issues.append(
                    QualityIssue(
                        rule_id="project.backend_layers_required",
                        summary=(
                            "后端必须具备接口、应用、基础设施、领域四类结构；"
                            f"当前缺少: {missing}"
                        ),
                    )
                )
            # The Project Contract is the source of truth for the runtime
            # entrypoint.  Older policy versions hard-coded ``main.py`` and
            # rejected valid standard-library services whose contract points
            # to ``app/server.py`` (or another explicitly owned composition
            # root).  When no contract exists, accept only the small set of
            # conventional entrypoint names so this remains a deterministic
            # safety check rather than a free-form file search.
            main = _resolve_backend_entrypoint(workspace, backend, app, contract)
            if main is None:
                issues.append(QualityIssue(
                    rule_id="project.backend_entrypoint_required",
                    summary=(
                        "后端缺少 Project Contract 声明或受信约定的启动入口"
                        if contract is None
                        else (
                            "Project Contract 声明的后端启动入口不存在: "
                            + str(contract.entrypoints.backend_file)
                        )
                    ),
                ))
            api_init = app / "api" / "__init__.py"
            api_router = app / "api" / "router.py"
            if api_init.is_file() and "router import" in api_init.read_text(
                encoding="utf-8", errors="replace"
            ) and not api_router.is_file():
                issues.append(QualityIssue(
                    rule_id="project.backend_api_router_required",
                    summary="backend/app/api/__init__.py 引用了 router，但缺少 backend/app/api/router.py",
                ))
            if main is not None and len(main.read_text(encoding="utf-8", errors="replace").splitlines()) > 240:
                issues.append(
                    QualityIssue(
                        rule_id="project.backend_entrypoint_size",
                        summary=f"后端入口 {main.relative_to(workspace)} 超过 240 行，入口文件疑似承载过多业务逻辑",
                    )
                )
            if application_id != "python-backend" and contract is not None and main is not None:
                main_text = main.read_text(encoding="utf-8", errors="replace")
                api_sources = "\n".join(
                    path.read_text(encoding="utf-8", errors="replace")
                    for path in app.rglob("*.py")
                ) if app.is_dir() else main_text
                if "FastAPI" in main_text and not any(
                    marker in api_sources for marker in ('"/health"', "'/health'")
                ):
                    issues.append(QualityIssue(
                        rule_id="project.runtime_health_endpoint_required",
                        summary="FastAPI 应用缺少 /health 就绪接口",
                    ))
                if not _has_database_init_entrypoint(root, workspace):
                    issues.append(QualityIssue(
                        rule_id="project.database_init_entrypoint_required",
                        summary="数据库项目缺少 migrate.py、init_db.py 或 operations/migrate.sh 初始化入口",
                    ))
            tests = workspace / "tests"
            if not tests.is_dir() or not any(t.is_file() for t in tests.rglob("*")):
                issues.append(
                    QualityIssue(
                        rule_id="project.backend_tests_required",
                        summary="后端项目缺少 workspace/tests 测试文件",
                    )
                )
        if contract is not None and application_id != "python-backend":
            issues.extend(_evaluate_project_contract(workspace, contract))

        runtime_preflight = ProjectRuntimePreflight().evaluate(str(root))
        issues.extend(runtime_preflight.issues)
        return QualityReport(self.policy_id, tuple(issues))

    def render(self, project_path: str) -> str:
        report = self.evaluate(project_path)
        if report.passed:
            return f"policy_id={report.policy_id}\nstatus=passed\nissues=0"
        lines = [f"policy_id={report.policy_id}", "status=failed"]
        lines.extend(f"- {issue.rule_id}: {issue.summary}" for issue in report.issues)
        return "\n".join(lines)


def _resolve_backend_entrypoint(
    workspace: Path,
    backend: Path,
    app: Path,
    contract: "ImplementationContract | None",
) -> Path | None:
    """Resolve a backend entrypoint without imposing a filename convention."""
    if contract is not None:
        relative = contract.entrypoints.backend_file
        if relative:
            candidate = workspace / relative.removeprefix("workspace/").lstrip("/")
            return candidate if candidate.is_file() else None
        return None
    for candidate in (backend / "main.py", app / "main.py", app / "server.py"):
        if candidate.is_file():
            return candidate
    return None


def _evaluate_project_contract(workspace: Path, contract: ImplementationContract) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    path_mapping = _effective_project_path_mapping(workspace, contract)
    layer_for_path: dict[str, str] = {}
    for path in (p for p in workspace.rglob("*") if p.is_file()):
        relative = path.relative_to(workspace).as_posix()
        matches = [layer for layer, patterns in path_mapping.items()
                   if any(_path_matches(relative, pattern) for pattern in patterns)]
        if matches:
            layer_for_path[relative] = matches[0]
        elif path.suffix == ".py" and any(part in relative for part in ("backend/", "frontend/")):
            issues.append(QualityIssue(
                rule_id="project.layer_path_boundary",
                summary=f"实现文件未落在 Project Contract 声明的路径中: {relative}",
            ))
    for relative, layer in layer_for_path.items():
        path = workspace / relative
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as error:
            issues.append(QualityIssue("project.layer_python_syntax", f"无法解析 {relative}: {error}"))
            continue
        forbidden = contract.forbidden_imports.get(layer, ())
        allowed = set(contract.allowed_dependencies.get(layer, ()))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                # Persistence imports are allowed at interface adapter edges
                # used solely for dependency injection and exception mapping.
                # Route modules remain forbidden from importing the ORM.
                adapter_import = (
                    layer == "interface"
                    and path.name in {"dependencies.py", "error_handlers.py"}
                    and (name == "sqlalchemy" or name.startswith("sqlalchemy."))
                )
                if any(name == item or name.startswith(item + ".") for item in forbidden) and not adapter_import:
                    issues.append(QualityIssue(
                        rule_id="project.layer_forbidden_import",
                        summary=f"{relative} ({layer}) 引用了禁止依赖 {name}",
                    ))
                target = _contract_layer_from_import(name, contract)
                if target and target != layer and target not in allowed and target not in {"infrastructure_ports", "application_ports"}:
                    issues.append(QualityIssue(
                        rule_id="project.layer_dependency_direction",
                        summary=f"{relative} ({layer}) 依赖 {target}，不在允许依赖列表中",
                    ))
    tests = workspace / "tests"
    test_files = [
        (
            path.relative_to(workspace).as_posix().lower(),
            path.read_text(encoding="utf-8", errors="replace").lower(),
        )
        for path in tests.rglob("*") if path.is_file()
    ] if tests.is_dir() else []
    # Test type evidence is intentionally semantic rather than a required
    # magic string.  Paths, imports and common driver/API markers all count,
    # so a project can name tests naturally without weakening the contract.
    type_markers: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "domain_unit": (("domain", "model", "entity"), ("domain", "model", "entity")),
        "application_unit": (("application", "service", "use_case"), ("application", "service", "use_case")),
        "api_http": (("/api/", "api_", "http", "client"), ("fastapi", "httpx", "requests", "api")),
        "web_unit": (("web", "frontend", ".test.js", ".spec.js"), ("frontend", "document", "fetch(")),
        "frontend": (("web", "frontend", ".test.js", ".spec.js"), ("frontend", "document", "fetch(")),
        "postgresql_integration": (
            ("postgres", "postgresql", "infrastructure", "integration"),
            ("postgresql", "postgres", "asyncpg", "database_url", "sqlalchemy", "testcontainers"),
        ),
        "concurrency": (
            ("concurr", "parallel", "stress", "load"),
            ("asyncio.gather", "asyncio.create_task", "concurrent", "oversell", "row lock", "idempotency"),
        ),
        "concurrency_integration": (
            ("concurr", "parallel", "stress", "load", "integration"),
            ("asyncio.gather", "asyncio.create_task", "concurrent", "oversell", "row lock", "idempotency", "postgres"),
        ),
        "frontend_interaction": (
            ("frontend", "web", ".test.js", ".spec.js", ".test.ts", ".test.tsx"),
            ("frontend", "document", "fetch(", "user-event", "testing-library"),
        ),
        "delivery_validation": (
            ("compose", "docker", "start.sh", "start.ps1", "readme", "smoke", "e2e", "delivery"),
            ("docker compose", "docker-compose", "uvicorn", "health", "startup", "curl", "playwright"),
        ),
    }
    for test_type in contract.required_test_types:
        path_markers, content_markers = type_markers.get(
            test_type, ((test_type.lower(),), (test_type.lower(),))
        )
        if not any(
            any(marker in relative for marker in path_markers)
            or any(marker in content for marker in content_markers)
            for relative, content in test_files
        ):
            issues.append(QualityIssue(
                rule_id="project.required_test_type_missing",
                summary=f"Project Contract 要求测试类型未找到证据: {test_type}",
            ))
    return issues


def _path_matches(relative: str, pattern: str) -> bool:
    """Match contract globs while retaining compatibility with ``**`` paths."""
    return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(
        relative, pattern.replace("**", "*")
    )


def _effective_project_path_mapping(
    workspace: Path, contract: ImplementationContract
) -> dict[str, tuple[str, ...]]:
    """Build the path view used by policy from one canonical contract.

    Entrypoints and interface owner files are contractual paths even when an
    architecture model only listed their directory in ``entrypoints`` or
    ``interfaces``.  The worker wrapper is a conventional operations entry
    point generated by ProjectOS.  Only existing, explicitly meaningful files
    are inferred; arbitrary unlisted files remain boundary violations.
    """
    mapping = {
        layer: list(patterns) for layer, patterns in (contract.path_mapping or {}).items()
    }

    def add(layer: str, relative: str | None) -> None:
        if not layer or layer not in mapping or not relative:
            return
        normalized = relative.removeprefix("workspace/").lstrip("/")
        if normalized and not any(_path_matches(normalized, pattern) for pattern in mapping[layer]):
            mapping[layer].append(normalized)

    # The canonical contract names the HTTP adapter layer ``interface``;
    # older contracts used ``api``. Prefer the declared interface layer.
    add("interface", contract.entrypoints.backend_file)
    add("api", contract.entrypoints.backend_file)
    add("frontend", contract.entrypoints.frontend_file)

    # Alembic files are infrastructure-owned even when the architecture
    # contract only lists the application directories explicitly.
    for migration in workspace.glob("backend/migrations/**/*.py"):
        add("infrastructure", migration.relative_to(workspace).as_posix())

    unit_layers = {unit.unit_id: unit.layer for unit in contract.units}
    for interface in contract.interfaces:
        add(unit_layers.get(interface.owner_unit, ""), interface.owner_file)

    if "operations" in mapping:
        for wrapper in ("backend/worker.py", "backend/app/worker.py"):
            if (workspace / wrapper).is_file():
                add("operations", wrapper)
    return {layer: tuple(patterns) for layer, patterns in mapping.items()}


def _contract_layer_from_import(name: str, contract: ImplementationContract) -> str | None:
    normalized = name.replace(".", "/")
    for layer, patterns in contract.path_mapping.items():
        prefix = next((pattern.split("/**", 1)[0].rstrip("/") for pattern in patterns if "/" in pattern), None)
        if prefix and (normalized.startswith(prefix) or normalized.startswith(prefix.replace("/", "."))):
            return layer
    return None
