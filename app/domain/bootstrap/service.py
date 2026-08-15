"""Bootstrap domain：只声明项目运行环境，不执行 Docker 或网络操作。"""

from __future__ import annotations

from pathlib import Path

from app.artifact.toolset import ArtifactToolSet
from app.runtime.manifest import RuntimeManifest


class BootstrapService:
    """管理 runtime.yaml 与 requirements.in，保留依赖解析给受控 Resolver。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path)
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="environment",
            readable_artifacts=("requirement", "architecture", "tasks"),
        )

    def configure_runtime(self, profile: str, dependencies: str = "") -> str:
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
            }
        )
        manifest.save(str(self._project_path))
        dependency_path = self._project_path / "requirements.in"
        if dependency_lines:
            dependency_path.write_text(dependency_lines + "\n", encoding="utf-8")
        elif dependency_path.exists():
            dependency_path.unlink()
        return f"已配置 runtime profile: {manifest.profile}"

    def inspect_runtime(self) -> str:
        try:
            manifest = RuntimeManifest.load(str(self._project_path))
        except (FileNotFoundError, ValueError) as error:
            return f"runtime_status=missing\nmessage={error}"
        dependencies = "present" if manifest.dependencies_file else "none"
        return f"runtime_status=configured\nprofile={manifest.profile}\ndependencies={dependencies}"

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_environment(self, content: str) -> str:
        return self._artifacts.save(content)
