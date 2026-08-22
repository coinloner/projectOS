"""节点输出的最小可量化质量规则。"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import fnmatch
from pathlib import Path

from app.artifact.repository import ArtifactRef
from app.workspace.git_repository import ChangeSet
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.architecture.layer_contract import LayerContract


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


class GitCodeIntegrationPolicy:
    """Git ChangeSet 版本的代码集成底线策略。

    该策略只验证控制面事实：分区是否齐全、ChangeSet 是否存在、文件是否
    留在分区声明的目录中，以及所有分区是否共享同一个 baseline。业务代码的
    正确性仍由 TestAgent 和后续质量门负责。
    """

    policy_id = "code.git-integration.v1"

    def evaluate(
        self,
        outputs: tuple[tuple[ArtifactRef, ChangeSet], ...],
        *,
        missing_refs: tuple[ArtifactRef, ...] = (),
    ) -> QualityReport:
        issues: list[QualityIssue] = []
        if not outputs:
            issues.append(
                QualityIssue(
                    rule_id="code.changesets_required",
                    summary="代码集成至少需要一个 Git ChangeSet。",
                )
            )

        slots = {ref.slot for ref, _ in outputs}
        for required_slot in ("backend", "frontend"):
            if required_slot not in slots:
                issues.append(
                    QualityIssue(
                        rule_id=f"code.{required_slot}_scope_required",
                        summary=f"缺少 {required_slot} Git ChangeSet。",
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
        if len(baselines) > 1:
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
            prefix = f"workspace/{ref.slot}/"
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
        return QualityReport(policy_id=self.policy_id, issues=tuple(issues))


class ProjectQualityPolicy:
    """项目交付级质量底线。

    这是确定性结构检查，不试图判断业务正确性；它只阻止明显的单体堆积和
    不可启动交付。业务行为仍由测试证据与 ReviewAgent 判断。
    """

    policy_id = "project.quality.v1"

    def evaluate(self, project_path: str) -> QualityReport:
        root = Path(project_path).resolve()
        workspace = root / "workspace"
        if not workspace.is_dir():
            return QualityReport(self.policy_id, ())
        files = [path for path in workspace.rglob("*") if path.is_file()]
        if not files:
            return QualityReport(self.policy_id, ())

        issues: list[QualityIssue] = []
        from app.domain.architecture.layer_contract import LayerContractStore
        contract_store = LayerContractStore(str(root))
        contract: LayerContract | None = None
        if (root / "architecture.md").is_file():
            if not contract_store.exists():
                issues.append(QualityIssue(
                    rule_id="project.layer_contract_required",
                    summary="项目存在架构或运行时交付物，但缺少 .projectos/architecture/layer-contract.json",
                ))
            else:
                try:
                    contract = contract_store.load()
                except (ValueError, OSError) as error:
                    issues.append(QualityIssue(
                        rule_id="project.layer_contract_invalid",
                        summary=f"Layer Contract 无法校验: {error}",
                    ))
        if (root / "runtime.yaml").is_file():
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
                "interface": any((app / name).exists() for name in ("api", "routers", "routes")),
                "application": any((app / name).exists() for name in ("services", "service", "use_cases", "application"))
                or any(path.name in {"service.py", "services.py", "use_cases.py"} for path in backend_files),
                "infrastructure": any((app / name).exists() for name in ("repositories", "repository", "infrastructure", "database"))
                or any(path.name in {"database.py", "repositories.py", "repository.py"} for path in backend_files),
                "domain": any((app / name).exists() for name in ("domain", "models", "schemas"))
                or any(path.name in {"models.py", "schemas.py"} for path in backend_files),
            }
            if sum(categories.values()) < 4:
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
            main = app / "main.py"
            if main.is_file() and len(main.read_text(encoding="utf-8", errors="replace").splitlines()) > 240:
                issues.append(
                    QualityIssue(
                        rule_id="project.backend_entrypoint_size",
                        summary="后端 main.py 超过 240 行，入口文件疑似承载过多业务逻辑",
                    )
                )
            if contract is not None and main.is_file():
                main_text = main.read_text(encoding="utf-8", errors="replace")
                if "FastAPI" in main_text and not any(
                    marker in main_text for marker in ('"/health"', "'/health'")
                ):
                    issues.append(QualityIssue(
                        rule_id="project.runtime_health_endpoint_required",
                        summary="FastAPI 应用缺少 /health 就绪接口",
                    ))
                if not any((backend / name).is_file() for name in ("migrate.py", "init_db.py")):
                    issues.append(QualityIssue(
                        rule_id="project.database_init_entrypoint_required",
                        summary="数据库项目缺少 migrate.py 或 init_db.py 初始化入口",
                    ))
            tests = workspace / "tests"
            if not tests.is_dir() or not any(t.is_file() for t in tests.rglob("*")):
                issues.append(
                    QualityIssue(
                        rule_id="project.backend_tests_required",
                        summary="后端项目缺少 workspace/tests 测试文件",
                    )
                )
        if contract is not None:
            issues.extend(_evaluate_layer_contract(workspace, contract))

        frontend = workspace / "frontend"
        if frontend.is_dir() and (frontend / "index.html").is_file():
            for name in ("app.js", "styles.css"):
                if not (frontend / name).is_file():
                    issues.append(
                        QualityIssue(
                            rule_id="project.frontend_asset_required",
                            summary=f"前端缺少标准文件: frontend/{name}",
                        )
                    )
        return QualityReport(self.policy_id, tuple(issues))

    def render(self, project_path: str) -> str:
        report = self.evaluate(project_path)
        if report.passed:
            return f"policy_id={report.policy_id}\nstatus=passed\nissues=0"
        lines = [f"policy_id={report.policy_id}", "status=failed"]
        lines.extend(f"- {issue.rule_id}: {issue.summary}" for issue in report.issues)
        return "\n".join(lines)


def _evaluate_layer_contract(workspace: Path, contract: LayerContract) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    layer_for_path: dict[str, str] = {}
    for path in (p for p in workspace.rglob("*") if p.is_file()):
        relative = path.relative_to(workspace).as_posix()
        matches = [layer for layer, patterns in contract.path_mapping.items()
                   if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.replace("**", "*")) for pattern in patterns)]
        if matches:
            layer_for_path[relative] = matches[0]
        elif path.suffix == ".py" and any(part in relative for part in ("backend/", "frontend/")):
            issues.append(QualityIssue(
                rule_id="project.layer_path_boundary",
                summary=f"实现文件未落在 Layer Contract 声明的路径中: {relative}",
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
                if any(name == item or name.startswith(item + ".") for item in forbidden):
                    issues.append(QualityIssue(
                        rule_id="project.layer_forbidden_import",
                        summary=f"{relative} ({layer}) 引用了禁止依赖 {name}",
                    ))
                target = _layer_from_import(name, contract)
                if target and target != layer and target not in allowed and target not in {"infrastructure_ports", "application_ports"}:
                    issues.append(QualityIssue(
                        rule_id="project.layer_dependency_direction",
                        summary=f"{relative} ({layer}) 依赖 {target}，不在允许依赖列表中",
                    ))
    tests = workspace / "tests"
    test_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in tests.rglob("*") if path.is_file()
    ) if tests.is_dir() else ""
    type_markers = {
        "domain_unit": ("domain", "model", "entity"),
        "application_unit": ("application", "service", "use_case"),
        "api_http": ("api", "http", "client"),
        "web_unit": ("web", "frontend", ".test.js"),
    }
    for test_type in contract.required_test_types:
        markers = type_markers.get(test_type, (test_type,))
        if not any(marker in test_text.lower() for marker in markers):
            issues.append(QualityIssue(
                rule_id="project.required_test_type_missing",
                summary=f"Layer Contract 要求测试类型未找到证据: {test_type}",
            ))
    return issues


def _layer_from_import(name: str, contract: LayerContract) -> str | None:
    normalized = name.replace(".", "/")
    for layer, patterns in contract.path_mapping.items():
        prefix = next((pattern.split("/**", 1)[0].rstrip("/") for pattern in patterns if "/" in pattern), None)
        if prefix and (normalized.startswith(prefix) or normalized.startswith(prefix.replace("/", "."))):
            return layer
    return None
