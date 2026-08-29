"""SandboxPolicy：集中定义默认执行权限。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.runtime.manifest import RuntimeCatalog, RuntimeManifest, RuntimeProfile


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    memory: str = "512m"
    cpus: str = "1"
    pids: int = 128
    # 依赖安装目标位于容器临时文件系统；常见 Web 栈的 wheel 解压需要更大空间。
    tmpfs_size: str = "256m"
    output_char_limit: int = 12_000


@dataclass(frozen=True)
class SandboxSpec:
    project_path: Path
    workspace_path: Path
    check_id: str
    profile: RuntimeProfile
    dependencies_file: Path | None
    wheel_cache: Path | None
    limits: SandboxLimits
    # 本次检查实际使用的受信镜像（check 可覆盖 profile 默认镜像，例如 node）。
    image: str
    # 可由受信 policy 根据依赖声明选择测试运行器；None 表示使用 profile 默认命令。
    command_override: tuple[str, ...] | None = None


class SandboxPolicy:
    """将不可信 Manifest 和 Agent check 请求转换为受控执行规格。"""

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()

    def create_spec(
        self,
        *,
        project_path: str,
        manifest: RuntimeManifest,
        check_id: str,
    ) -> SandboxSpec:
        profile = RuntimeCatalog.get(manifest.profile)
        check = profile.checks.get(check_id)
        if check is None:
            available = ", ".join(sorted(profile.checks))
            raise PermissionError(
                f"profile '{profile.id}' 不允许执行 check '{check_id}'，可用: {available}"
            )
        root = Path(project_path).resolve()
        workspace = root / "workspace"
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace 不存在: {workspace}")

        dependencies_file: Path | None = None
        wheel_cache: Path | None = None
        if manifest.dependencies_file:
            dependencies_file = root / manifest.dependencies_file
            if not dependencies_file.is_file():
                raise FileNotFoundError(f"依赖声明不存在: {dependencies_file}")
            _validate_requirements_in(dependencies_file)
            digest = _sha256(dependencies_file)
            wheel_cache = root / ".sandbox" / "wheels" / digest
            ready_marker = wheel_cache / ".projectos-ready"
            if not wheel_cache.is_dir() or not ready_marker.is_file():
                raise FileNotFoundError(
                    "依赖缓存不存在；请先由受控 DependencyResolver 准备环境"
                )
            if ready_marker.read_text(encoding="utf-8").strip() != digest:
                raise ValueError("依赖缓存与当前 requirements.in 不匹配")

        command_override: tuple[str, ...] | None = None
        # python-pip projects commonly use pytest-style async/function tests,
        # which unittest silently treats as an empty suite. Select pytest only
        # when it is explicitly declared; stdlib profiles retain unittest.
        if (
            check_id == "unit"
            and dependencies_file is not None
            and _declares_pytest(dependencies_file)
        ):
            command_override = _pytest_unit_command(workspace)
        elif check_id == "web-unit":
            web_tests = discover_web_tests(root)
            if web_tests:
                # Node's no-argument discovery ignores common generated names
                # such as test_frontend.js. Pass the trusted, workspace-local
                # file list explicitly so every file accepted by preflight is
                # actually executed.
                command_override = ("node", "--test", *web_tests)

        return SandboxSpec(
            project_path=root,
            workspace_path=workspace,
            check_id=check_id,
            profile=profile,
            dependencies_file=dependencies_file,
            wheel_cache=wheel_cache,
            limits=self._limits,
            image=check.image or profile.image,
            command_override=command_override,
        )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_requirements_in(path: Path) -> None:
    """限制依赖声明为普通 PyPI 包规格，不接受 URL、路径或 pip 参数。"""
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        forbidden = ("--", "://", "/", "\\", "@", ";", "-e ")
        if any(token in line for token in forbidden):
            raise ValueError(
                f"requirements.in 第 {number} 行包含不允许的依赖来源或 pip 参数"
            )


def _declares_pytest(path: Path) -> bool:
    """Return True only for a direct pytest dependency declaration."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().lower()
        if not line or line.startswith("#"):
            continue
        package = line.split("[", 1)[0]
        package = package.split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0]
        package = package.split("~=", 1)[0].split("!=", 1)[0].strip()
        if package == "pytest":
            return True
    return False


def _pytest_unit_command(workspace: Path) -> tuple[str, ...]:
    """Build a deterministic unit-only pytest command for mixed projects.

    Integration/API suites often require a live database or network and are
    intentionally verified by their dedicated delivery checks.  Running them
    inside the network-isolated unit sandbox produces misleading failures.
    Preserve the historical all-tests command for projects without a
    conventional ``tests/unit`` tree.
    """
    unit_root = workspace / "tests" / "unit"
    if unit_root.is_dir() and any(path.is_file() for path in unit_root.rglob("*")):
        return ("python", "-m", "pytest", "-q", "tests/unit")
    return ("python", "-m", "pytest", "-q")


def discover_web_tests(project_path: str | Path) -> tuple[str, ...]:
    """Return trusted workspace-relative Node test files in stable order."""
    workspace = Path(project_path).resolve() / "workspace"
    if not workspace.is_dir():
        return ()
    return tuple(
        path.relative_to(workspace).as_posix()
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
        and path.suffix in {".js", ".mjs"}
        if (
            path.name.startswith("test-")
            or path.name.startswith("test_")
            or path.name.endswith(".test.js")
            or path.name.endswith(".test.mjs")
            or path.name.endswith("_test.js")
        )
    )
