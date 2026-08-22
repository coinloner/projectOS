"""项目 ID 到本地项目目录的持久化映射。"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock


class ProjectPathRegistry:
    """默认使用 projects/<id>，也支持用户在创建时指定本地目录。"""

    def __init__(self, projects_root: str | Path) -> None:
        self._root = Path(projects_root).resolve()
        self._path = self._root / ".projectos" / "project-paths.json"
        self._lock = RLock()

    def resolve(self, project_id: str) -> Path:
        with self._lock:
            mapping = self._load()
        configured = mapping.get(project_id)
        return Path(configured).resolve() if configured else (self._root / project_id).resolve()

    def register(self, project_id: str, project_path: str | Path) -> Path:
        path = Path(project_path).expanduser().resolve()
        with self._lock:
            mapping = self._load()
            mapping[project_id] = str(path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return path

    def default_path(self, project_id: str) -> Path:
        return (self._root / project_id).resolve()

    def _load(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}
