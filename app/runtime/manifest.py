"""项目运行时声明及受信任 Profile 目录。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.runtime.application import ApplicationCatalog


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    image: str
    checks: dict[str, tuple[str, ...]]
    supports_dependencies: bool = False


class RuntimeCatalog:
    """ProjectOS 可执行的运行时白名单，而非 Agent 可编辑配置。"""

    _PROFILES = {
        "python-stdlib": RuntimeProfile(
            id="python-stdlib",
            image="python:3.12-slim",
            checks={
                "unit": (
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                )
            },
        ),
        "python-pip": RuntimeProfile(
            id="python-pip",
            image="python:3.12-slim",
            checks={
                "unit": (
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                )
            },
            supports_dependencies=True,
        ),
    }

    @classmethod
    def get(cls, profile_id: str) -> RuntimeProfile:
        try:
            return cls._PROFILES[profile_id]
        except KeyError as error:
            available = ", ".join(sorted(cls._PROFILES))
            raise ValueError(
                f"不支持的 runtime profile '{profile_id}'，可用: {available}"
            ) from error


@dataclass(frozen=True)
class RuntimeManifest:
    """项目声明的、尚未可信的运行时配置。"""

    version: int
    profile: str
    dependencies_file: str | None = None
    application: str | None = None

    FILENAME = "runtime.yaml"

    @classmethod
    def load(cls, project_path: str) -> "RuntimeManifest":
        path = Path(project_path) / cls.FILENAME
        if not path.exists():
            raise FileNotFoundError(f"缺少运行时声明: {path}")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ValueError(f"运行时声明不是有效 YAML: {error}") from error
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: object) -> "RuntimeManifest":
        if not isinstance(payload, dict):
            raise ValueError("runtime.yaml 必须是 object")
        allowed = {"version", "profile", "dependencies_file", "application"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"runtime.yaml 包含未知字段: {', '.join(sorted(unknown))}")
        version = payload.get("version")
        profile = payload.get("profile")
        dependencies_file = payload.get("dependencies_file")
        application = payload.get("application")
        if version != 1:
            raise ValueError("runtime.yaml.version 当前必须为 1")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("runtime.yaml.profile 必须是非空字符串")
        if dependencies_file is not None and dependencies_file != "requirements.in":
            raise ValueError("当前只允许 dependencies_file: requirements.in")
        if application is not None and (
            not isinstance(application, str) or not application.strip()
        ):
            raise ValueError("runtime.yaml.application 必须是非空字符串")
        manifest = cls(
            version=version,
            profile=profile.strip(),
            dependencies_file=dependencies_file,
            application=application.strip() if application else None,
        )
        runtime_profile = RuntimeCatalog.get(manifest.profile)
        if manifest.dependencies_file and not runtime_profile.supports_dependencies:
            raise ValueError(f"profile '{manifest.profile}' 不支持第三方依赖")
        if manifest.application:
            ApplicationCatalog.get(manifest.application)
        return manifest

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"version": self.version, "profile": self.profile}
        if self.dependencies_file is not None:
            payload["dependencies_file"] = self.dependencies_file
        if self.application is not None:
            payload["application"] = self.application
        return payload

    def save(self, project_path: str) -> None:
        path = Path(project_path) / self.FILENAME
        path.write_text(
            yaml.safe_dump(self.as_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
