"""Bootstrap domain：只声明项目运行环境，不执行 Docker 或网络操作。"""

from __future__ import annotations

from pathlib import Path

from app.application.environment import EnvironmentProvisioner
from app.artifact.toolset import ArtifactToolSet
from app.runtime.application import ApplicationCatalog
from app.runtime.manifest import RuntimeManifest


class BootstrapService:
    """管理 runtime.yaml 与 requirements.in，保留依赖解析给受控 Resolver。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path)
        self._provisioner = EnvironmentProvisioner()
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="environment",
            readable_artifacts=("requirement", "architecture", "architecture_contract", "tasks"),
        )

    def configure_runtime(
        self,
        profile: str,
        dependencies: str = "",
        application: str | None = None,
    ) -> str:
        if application is not None:
            # 提前校验，让 Agent 从报错里直接看到受信应用白名单。
            ApplicationCatalog.get(application.strip())
        dependency_lines = dependencies.strip()
        manifest = RuntimeManifest.from_dict(
            {
                "version": 1,
                "profile": profile,
                **(
                    {"dependencies_file": "requirements.in"}
                    if dependency_lines
                    else {}
                ),
                **({"application": application.strip()} if application else {}),
            }
        )
        manifest.save(str(self._project_path))
        dependency_path = self._project_path / "requirements.in"
        if dependency_lines:
            dependency_path.write_text(dependency_lines + "\n", encoding="utf-8")
        elif dependency_path.exists():
            dependency_path.unlink()
        application_note = f"，application: {manifest.application}" if manifest.application else ""
        return f"已配置 runtime profile: {manifest.profile}{application_note}"

    def inspect_runtime(self) -> str:
        try:
            manifest = RuntimeManifest.load(str(self._project_path))
        except (FileNotFoundError, ValueError) as error:
            return f"runtime_status=missing\nmessage={error}"
        dependencies = "present" if manifest.dependencies_file else "none"
        return f"runtime_status=configured\nprofile={manifest.profile}\ndependencies={dependencies}"

    def prepare_environment(self) -> str:
        """通过控制平面准备白名单镜像；Agent 不获得 Docker 命令权限。"""
        approval = self._provisioner.dependency_approval(str(self._project_path))
        result = self._provisioner.prepare(
            str(self._project_path),
            # The approval endpoint persists a digest-scoped decision.  Reuse
            # that decision here so a resumed EnvironmentAgent does not lose
            # the owner's approval and deterministically block again.
            dependencies_approved=bool(approval.get("approved", False)),
        )
        return "\n".join(
            [
                f"environment_status={result.status}",
                f"profile={result.profile or 'none'}",
                f"image={result.image or 'none'}",
                f"dependencies={result.dependencies}",
                f"attempts={result.attempts}",
                *([f"failure_kind={result.failure_kind}"] if result.failure_kind else []),
                *([f"recovery_actions={','.join(result.recovery_actions)}"] if result.recovery_actions else []),
                *([f"message={result.message}"] if result.message else []),
            ]
        )

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_environment(self, content: str) -> str:
        return self._artifacts.save(content)
