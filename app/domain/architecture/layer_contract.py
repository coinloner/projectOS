"""Architecture 节点产出的结构化分层契约。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


_REQUIRED_KEYS = {
    "schema_version",
    "layers",
    "allowed_dependencies",
    "forbidden_imports",
    "required_test_types",
    "path_mapping",
}


@dataclass(frozen=True)
class LayerContract:
    schema_version: int
    layers: tuple[str, ...]
    allowed_dependencies: dict[str, tuple[str, ...]]
    forbidden_imports: dict[str, tuple[str, ...]]
    required_test_types: tuple[str, ...]
    path_mapping: dict[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "layers": list(self.layers),
            "allowed_dependencies": {k: list(v) for k, v in self.allowed_dependencies.items()},
            "forbidden_imports": {k: list(v) for k, v in self.forbidden_imports.items()},
            "required_test_types": list(self.required_test_types),
            "path_mapping": {k: list(v) for k, v in self.path_mapping.items()},
        }

    def summary(self) -> dict[str, Any]:
        """Planner/Agent 可见的短摘要，不包含实现文件正文。"""
        return {
            "exists": True,
            "schema_version": self.schema_version,
            "layers": list(self.layers),
            "required_test_types": list(self.required_test_types),
            "path_mapping": self.as_dict()["path_mapping"],
        }

    @classmethod
    def parse(cls, content: str) -> "LayerContract":
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(f"Layer Contract 不是有效 JSON: {error}") from error
        if not isinstance(raw, dict) or not _REQUIRED_KEYS.issubset(raw):
            missing = sorted(_REQUIRED_KEYS - set(raw or {}))
            raise ValueError(f"Layer Contract 缺少字段: {', '.join(missing)}")
        if raw.get("schema_version") != 1:
            raise ValueError("Layer Contract schema_version 必须为 1")
        layers = _strings(raw["layers"], "layers")
        if not layers:
            raise ValueError("Layer Contract 至少声明一层")
        return cls(
            schema_version=1,
            layers=layers,
            allowed_dependencies=_mapping(raw["allowed_dependencies"], layers, "allowed_dependencies"),
            forbidden_imports=_mapping(raw["forbidden_imports"], layers, "forbidden_imports", allow_missing=True),
            required_test_types=_strings(raw["required_test_types"], "required_test_types"),
            path_mapping=_mapping(raw["path_mapping"], layers, "path_mapping"),
        )


class LayerContractStore:
    """在受控的 .projectos/architecture 目录读写契约。"""

    relative_path = ".projectos/architecture/layer-contract.json"

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / self.relative_path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> LayerContract:
        if not self.exists():
            raise FileNotFoundError(self.relative_path)
        return LayerContract.parse(self._path.read_text(encoding="utf-8"))

    def save(self, content: str) -> LayerContract:
        contract = LayerContract.parse(content)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(contract.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return contract


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Layer Contract.{name} 必须是非空字符串数组")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _mapping(value: Any, layers: tuple[str, ...], name: str, *, allow_missing: bool = False) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError(f"Layer Contract.{name} 必须是对象")
    unknown = set(value) - set(layers)
    if unknown:
        raise ValueError(f"Layer Contract.{name} 包含未声明层: {', '.join(sorted(unknown))}")
    result: dict[str, tuple[str, ...]] = {}
    for layer in layers:
        if layer not in value:
            if allow_missing:
                result[layer] = ()
                continue
            raise ValueError(f"Layer Contract.{name} 缺少层: {layer}")
        result[layer] = _strings(value[layer], f"{name}.{layer}") if value[layer] else ()
    return result
