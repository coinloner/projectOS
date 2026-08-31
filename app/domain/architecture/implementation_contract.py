"""Architecture 产出的结构化实现合同。

该合同是 Architecture 与 CodeAgent 之间的稳定边界。Markdown 用于人类阅读，
本对象用于编译 WorkItem、校验路径和断点恢复。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True)
class EntrypointContract:
    """项目可执行入口的单一来源。"""

    backend_file: str | None = None
    backend_import: str | None = None
    backend_command: str | None = None
    frontend_file: str | None = None
    health_path: str = "/health"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "backend_file": self.backend_file,
            "backend_import": self.backend_import,
            "backend_command": self.backend_command,
            "frontend_file": self.frontend_file,
            "health_path": self.health_path,
        }

    @classmethod
    def parse(cls, raw: Any) -> "EntrypointContract":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("Implementation Contract.entrypoints 必须是对象")

        def optional(name: str) -> str | None:
            value = raw.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                return None
            if not isinstance(value, str):
                raise ValueError(f"entrypoints.{name} 必须是字符串")
            return value.strip()

        health = raw.get("health_path", "/health")
        if not isinstance(health, str) or not health.strip():
            raise ValueError("entrypoints.health_path 必须是非空字符串")
        return cls(
            backend_file=optional("backend_file"),
            backend_import=optional("backend_import"),
            backend_command=optional("backend_command"),
            frontend_file=optional("frontend_file"),
            health_path=health.strip(),
        )


@dataclass(frozen=True)
class InterfaceContract:
    """机器可读的跨单元接口/符号契约。"""

    interface_id: str
    kind: str
    name: str
    owner_unit: str
    owner_file: str | None = None
    signature: str | None = None
    input_schema: str | None = None
    output_schema: str | None = None
    errors: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("interface_id", "kind", "name", "owner_unit"):
            if not getattr(self, name).strip():
                raise ValueError(f"InterfaceContract.{name} 不能为空")
        # LLMs commonly use equivalent vocabulary such as ``endpoint`` or
        # ``class``.  Normalize those aliases at the contract boundary so a
        # harmless naming choice does not consume a retry or become a bogus
        # capability request.  Unknown values remain hard validation errors.
        aliases = {
            "endpoint": "api",
            "http": "api",
            "http_endpoint": "api",
            "api_endpoint": "api",
            "route": "api",
            "class": "symbol",
            "function": "symbol",
            "method": "symbol",
            "model": "data",
            "schema": "data",
            "message": "event",
            "topic": "event",
            "repository": "service",
            "repo": "service",
            "router": "api",
            "route_handler": "api",
            "controller": "api",
            "dto": "data",
            "model_schema": "data",
            "queue": "event",
            # Architecture workers sometimes use ``provided``/``consumed``
            # as a direction label in the contract's ``kind`` field.  The
            # contract model has no separate direction field; retain the
            # interface as a service boundary instead of rejecting a
            # semantically valid hand-off.
            "provided": "service",
            "consumed": "service",
            "exception": "symbol",
            "entrypoint": "api",
            "lifecycle": "service",
            "dependency": "service",
        }
        normalized_kind = aliases.get(self.kind.strip().lower(), self.kind.strip().lower())
        object.__setattr__(self, "kind", normalized_kind)
        if normalized_kind not in {"symbol", "service", "api", "data", "event"}:
            raise ValueError("InterfaceContract.kind 必须是 symbol/service/api/data/event")

    def as_dict(self) -> dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "kind": self.kind,
            "name": self.name,
            "owner_unit": self.owner_unit,
            "owner_file": self.owner_file,
            "signature": self.signature,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "errors": list(self.errors),
            "constraints": list(self.constraints),
        }

    @classmethod
    def parse(cls, raw: Any) -> "InterfaceContract":
        if not isinstance(raw, dict):
            raise ValueError("interfaces 中的元素必须是对象")
        return cls(
            interface_id=str(raw.get("interface_id", raw.get("id", ""))).strip(),
            kind=str(raw.get("kind", "symbol")).strip().lower(),
            name=str(raw.get("name", "")).strip(),
            owner_unit=str(raw.get("owner_unit", raw.get("provider", ""))).strip(),
            owner_file=_optional_string(raw.get("owner_file")),
            signature=_optional_string(raw.get("signature")),
            input_schema=_optional_string(raw.get("input_schema")),
            output_schema=_optional_string(raw.get("output_schema")),
            errors=_strings(raw.get("errors", []), "interfaces.errors", allow_empty=True),
            constraints=_strings(raw.get("constraints", []), "interfaces.constraints", allow_empty=True),
        )


@dataclass(frozen=True)
class ImplementationUnit:
    unit_id: str
    layer: str
    objective: str
    allowed_paths: tuple[str, ...]
    required_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ("architecture", "environment")
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    parallel_group: str | None = None
    output_key: str | None = None
    output_slot: str | None = None
    requirement_ids: tuple[str, ...] = ()
    wave: int | None = None
    owned_files: tuple[str, ...] = ()
    provides_interfaces: tuple[str, ...] = ()
    consumes_interfaces: tuple[str, ...] = ()
    provided_symbols: tuple[str, ...] = ()
    required_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("unit_id", "layer", "objective"):
            if not getattr(self, name).strip():
                raise ValueError(f"ImplementationUnit.{name} 不能为空")
        if not self.allowed_paths:
            raise ValueError("ImplementationUnit.allowed_paths 不能为空")
        if any(not value.strip() for values in (
            self.allowed_paths, self.required_paths, self.forbidden_paths,
            self.depends_on, self.input_refs, self.acceptance_criteria,
            self.constraints, self.non_goals, self.policy_refs, self.skill_refs,
            self.requirement_ids,
            self.provides_interfaces, self.consumes_interfaces,
            self.provided_symbols, self.required_symbols,
        ) for value in values):
            raise ValueError("ImplementationUnit 的字符串字段不能包含空值")
        _validate_concrete_files(
            self.owned_files,
            field=f"实现单元 '{self.unit_id}' 的 owned_files",
        )
        _validate_concrete_files(
            self.required_paths,
            field=f"实现单元 '{self.unit_id}' 的 required_paths",
        )
        if self.owned_files and any(path not in self.owned_files for path in self.required_paths):
            extra = sorted(set(self.required_paths) - set(self.owned_files))
            raise ValueError(
                f"实现单元 '{self.unit_id}' 的 required_paths 必须属于 owned_files；"
                f"跨文件依赖应通过 depends_on/接口契约声明: {', '.join(extra)}"
            )
        if self.wave is not None and self.wave < 0:
            raise ValueError("ImplementationUnit.wave 不能小于 0")

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "layer": self.layer,
            "objective": self.objective,
            "allowed_paths": list(self.allowed_paths),
            "required_paths": list(self.required_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "depends_on": list(self.depends_on),
            "input_refs": list(self.input_refs),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "policy_refs": list(self.policy_refs),
            "skill_refs": list(self.skill_refs),
            "parallel_group": self.parallel_group,
            "output_key": self.output_key,
            "output_slot": self.output_slot,
            "requirement_ids": list(self.requirement_ids),
            "wave": self.wave,
            "owned_files": list(self.owned_files),
            "provides_interfaces": list(self.provides_interfaces),
            "consumes_interfaces": list(self.consumes_interfaces),
            "provided_symbols": list(self.provided_symbols),
            "required_symbols": list(self.required_symbols),
        }

    @classmethod
    def parse(cls, raw: Any) -> "ImplementationUnit":
        if not isinstance(raw, dict):
            raise ValueError("implementation_units 中的元素必须是对象")
        allowed_paths = _strings(raw.get("allowed_paths", []), "allowed_paths", allow_empty=True)
        # v1 兼容的目录授权别名：目录根自动规范化为 glob，required_paths
        # 仍然用于声明必须出现的入口文件。
        allowed_roots = _strings(raw.get("allowed_roots", []), "allowed_roots", allow_empty=True)
        normalized_roots = tuple(
            root.rstrip("/") + "/**" for root in allowed_roots
        )
        allowed_paths = tuple(dict.fromkeys((*allowed_paths, *normalized_roots)))
        required_value = raw.get("required_paths")
        if required_value in (None, []):
            required_value = raw.get("required_files", [])
        return cls(
            unit_id=str(raw.get("unit_id", "")).strip(),
            layer=str(raw.get("layer", "")).strip(),
            objective=str(raw.get("objective", "")).strip(),
            allowed_paths=allowed_paths,
            required_paths=_strings(required_value, "required_paths", allow_empty=True),
            forbidden_paths=_strings(raw.get("forbidden_paths", []), "forbidden_paths", allow_empty=True),
            depends_on=_strings(raw.get("depends_on", []), "depends_on", allow_empty=True),
            input_refs=_strings(raw.get("input_refs", ["architecture", "environment"]), "input_refs", allow_empty=True),
            acceptance_criteria=_strings(raw.get("acceptance_criteria", []), "acceptance_criteria", allow_empty=True),
            constraints=_strings(raw.get("constraints", []), "constraints", allow_empty=True),
            non_goals=_strings(raw.get("non_goals", []), "non_goals", allow_empty=True),
            policy_refs=_strings(raw.get("policy_refs", []), "policy_refs", allow_empty=True),
            skill_refs=_strings(raw.get("skill_refs", []), "skill_refs", allow_empty=True),
            parallel_group=_optional_string(raw.get("parallel_group")),
            output_key=_optional_string(raw.get("output_key")),
            output_slot=_optional_string(raw.get("output_slot")),
            requirement_ids=_strings(raw.get("requirement_ids", []), "requirement_ids", allow_empty=True),
            wave=_optional_int(raw.get("wave")),
            owned_files=_strings(raw.get("owned_files", []), "owned_files", allow_empty=True),
            provides_interfaces=_strings(raw.get("provides_interfaces", raw.get("provides", [])), "provides_interfaces", allow_empty=True),
            consumes_interfaces=_strings(raw.get("consumes_interfaces", raw.get("consumes", [])), "consumes_interfaces", allow_empty=True),
            provided_symbols=_strings(raw.get("provided_symbols", []), "provided_symbols", allow_empty=True),
            required_symbols=_strings(raw.get("required_symbols", []), "required_symbols", allow_empty=True),
        )


@dataclass(frozen=True)
class ImplementationContract:
    schema_version: int
    units: tuple[ImplementationUnit, ...]
    entrypoints: EntrypointContract = EntrypointContract()
    required_files: tuple[str, ...] = ()
    interfaces: tuple[InterfaceContract, ...] = ()
    # Architecture layer rules live in the same canonical project contract as
    # file-level implementation units.  They are kept as immutable mappings so
    # Policy, Compiler and Integration cannot read divergent contract files.
    layers: tuple[str, ...] = ()
    allowed_dependencies: dict[str, tuple[str, ...]] | None = None
    forbidden_imports: dict[str, tuple[str, ...]] | None = None
    required_test_types: tuple[str, ...] = ()
    path_mapping: dict[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Implementation Contract schema_version 必须为 1")
        ids = [unit.unit_id for unit in self.units]
        if not ids:
            raise ValueError("Implementation Contract 至少需要一个实现单元")
        if len(ids) != len(set(ids)):
            raise ValueError("Implementation Contract 包含重复 unit_id")
        known = set(ids)
        interface_ids = [interface.interface_id for interface in self.interfaces]
        if len(interface_ids) != len(set(interface_ids)):
            raise ValueError("Implementation Contract.interfaces 包含重复 interface_id")
        interfaces_by_id = {interface.interface_id: interface for interface in self.interfaces}
        for interface in self.interfaces:
            if interface.owner_unit not in known:
                raise ValueError(f"接口 '{interface.interface_id}' 的 owner_unit 不存在: {interface.owner_unit}")
        for unit in self.units:
            unknown = set(unit.depends_on) - known
            if unknown:
                raise ValueError(f"实现单元 '{unit.unit_id}' 依赖不存在的单元: {', '.join(sorted(unknown))}")
            if unit.unit_id in unit.depends_on:
                raise ValueError(f"实现单元 '{unit.unit_id}' 不能依赖自身")
            referenced = (*unit.provides_interfaces, *unit.consumes_interfaces)
            unknown_interfaces = set(referenced) - set(interfaces_by_id)
            if unknown_interfaces:
                raise ValueError(f"实现单元 '{unit.unit_id}' 引用了不存在的接口: {', '.join(sorted(unknown_interfaces))}")
        seen_paths: dict[str, str] = {}
        seen_owned_files: dict[str, str] = {}
        for unit in self.units:
            for pattern in unit.allowed_paths:
                for previous, previous_unit in seen_paths.items():
                    if _paths_overlap(previous, pattern) and not _scopes_are_file_disjoint(
                        unit, previous_unit, self.units
                    ):
                        raise ValueError(
                            f"实现单元 '{unit.unit_id}' 与 '{previous_unit}' 的允许路径重叠: {pattern}"
                        )
                seen_paths[pattern] = unit.unit_id
            for owned in unit.owned_files:
                if not any(_matches_path(owned, pattern) for pattern in unit.allowed_paths):
                    raise ValueError(
                        f"实现单元 '{unit.unit_id}' 的 owned_files 越过 allowed_paths: {owned}"
                    )
                previous = seen_owned_files.get(owned)
                if previous is not None and previous != unit.unit_id:
                    raise ValueError(f"完整文件 '{owned}' 只能由一个实现单元负责")
                seen_owned_files[owned] = unit.unit_id
        if any(not path.strip() for path in self.required_files):
            raise ValueError("Implementation Contract.required_files 不能包含空值")
        _validate_concrete_files(
            self.required_files,
            field="Implementation Contract.required_files",
        )
        if len(set(self.required_files)) != len(self.required_files):
            raise ValueError("Implementation Contract.required_files 不能重复")
        layers = tuple(dict.fromkeys(layer.strip() for layer in self.layers if layer.strip()))
        object.__setattr__(self, "layers", layers)
        allowed = _normalize_layer_mapping(
            self.allowed_dependencies or {}, layers, "allowed_dependencies"
        )
        forbidden = _normalize_layer_mapping(
            self.forbidden_imports or {}, layers, "forbidden_imports", allow_missing=True
        )
        paths = _normalize_layer_mapping(
            self.path_mapping or {}, layers, "path_mapping"
        )
        object.__setattr__(self, "allowed_dependencies", allowed)
        object.__setattr__(self, "forbidden_imports", forbidden)
        object.__setattr__(self, "path_mapping", paths)
        if not self.required_test_types:
            object.__setattr__(self, "required_test_types", ())
        self._ensure_acyclic()

    def _ensure_acyclic(self) -> None:
        pending = {unit.unit_id: set(unit.depends_on) for unit in self.units}
        resolved: set[str] = set()
        while pending:
            ready = {key for key, deps in pending.items() if deps <= resolved}
            if not ready:
                raise ValueError("Implementation Contract 存在循环依赖")
            resolved.update(ready)
            for key in ready:
                del pending[key]

    @property
    def layer_summary(self) -> dict[str, Any]:
        """Return the layer-policy projection used by Policy and tooling."""
        return {
            "schema_version": self.schema_version,
            "layers": list(self.layers),
            "allowed_dependencies": {
                key: list(value) for key, value in (self.allowed_dependencies or {}).items()
            },
            "forbidden_imports": {
                key: list(value) for key, value in (self.forbidden_imports or {}).items()
            },
            "required_test_types": list(self.required_test_types),
            "path_mapping": {
                key: list(value) for key, value in (self.path_mapping or {}).items()
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entrypoints": self.entrypoints.as_dict(),
            "required_files": list(self.required_files),
            "interfaces": [interface.as_dict() for interface in self.interfaces],
            "layers": list(self.layers),
            "allowed_dependencies": {
                key: list(value) for key, value in (self.allowed_dependencies or {}).items()
            },
            "forbidden_imports": {
                key: list(value) for key, value in (self.forbidden_imports or {}).items()
            },
            "required_test_types": list(self.required_test_types),
            "path_mapping": {
                key: list(value) for key, value in (self.path_mapping or {}).items()
            },
            "implementation_units": [u.as_dict() for u in self.units],
        }

    @classmethod
    def parse(cls, content: str | dict[str, Any]) -> "ImplementationContract":
        try:
            raw = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError as error:
            raise ValueError(f"Implementation Contract 不是有效 JSON: {error}") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Implementation Contract 必须包含 schema_version=1")
        units_raw = raw.get("implementation_units")
        if not isinstance(units_raw, list):
            raise ValueError("Implementation Contract.implementation_units 必须是数组")
        return cls(
            1,
            tuple(ImplementationUnit.parse(item) for item in units_raw),
            entrypoints=EntrypointContract.parse(raw.get("entrypoints", raw.get("entrypoint"))),
            required_files=_strings(raw.get("required_files", []), "required_files", allow_empty=True),
            interfaces=tuple(InterfaceContract.parse(item) for item in raw.get("interfaces", [])),
            layers=_strings(raw.get("layers", []), "layers", allow_empty=True),
            allowed_dependencies=_raw_layer_mapping(raw.get("allowed_dependencies", {}), "allowed_dependencies"),
            forbidden_imports=_raw_layer_mapping(raw.get("forbidden_imports", {}), "forbidden_imports"),
            required_test_types=_strings(raw.get("required_test_types", []), "required_test_types", allow_empty=True),
            path_mapping=_raw_layer_mapping(raw.get("path_mapping", {}), "path_mapping"),
        )


class ImplementationContractStore:
    """唯一的项目架构/实现合同存储。

    ``implementation-contract.json`` 只作为历史 Trace 的只读迁移来源；新
    运行和所有写入均使用 ``project-contract.json``。
    """

    relative_path = ".projectos/architecture/project-contract.json"
    legacy_relative_path = ".projectos/architecture/implementation-contract.json"

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / self.relative_path

    def exists(self) -> bool:
        return self._path.is_file() or (self._path.parent / self.legacy_relative_path.rsplit("/", 1)[-1]).is_file()

    def load(self) -> ImplementationContract:
        path = self._path
        if not path.is_file():
            legacy = self._path.parent / self.legacy_relative_path.rsplit("/", 1)[-1]
            if not legacy.is_file():
                raise FileNotFoundError(self.relative_path)
            path = legacy
        return ImplementationContract.parse(path.read_text(encoding="utf-8"))

    def save(self, content: str | dict[str, Any]) -> ImplementationContract:
        contract = ImplementationContract.parse(content)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(contract.as_dict(), ensure_ascii=False, indent=2) + "\n"
        # Validation happens before this point.  Write and fsync a sibling
        # temporary file, then atomically replace the canonical path so a
        # process restart can observe either the previous complete contract or
        # the new complete contract, never a truncated JSON document.
        fd, temporary_name = tempfile.mkstemp(
            prefix=".project-contract.", suffix=".tmp", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return contract


# Public name for the canonical project-level contract.  The old class name is
# retained in code references during the migration, but both names resolve to
# the same object and the store has only one new write location.
ProjectContract = ImplementationContract
ProjectContractStore = ImplementationContractStore


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    # LLM 常把单个引用写成字符串；控制面可无损规范化为单元素数组。
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        if allow_empty and value == []:
            return ()
        # Keep the full field path supplied by the caller.  Previously every
        # validation error was labelled ``ImplementationUnit.*`` which made a
        # malformed top-level ProjectContract.layers look like an unsupported
        # unit field to the LLM and triggered a bogus capability request.
        raise ValueError(f"{name} 必须是字符串数组")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, str):
        raise ValueError("可选字符串字段不能是空值")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("可选 wave 必须是非负整数")
    if value < 0:
        raise ValueError("可选 wave 必须是非负整数")
    return value


def _validate_concrete_files(paths: tuple[str, ...], *, field: str) -> None:
    """Reject directory/glob values where a delivery file is required.

    ``allowed_paths`` is intentionally not passed here: it is the authorization
    boundary and may contain directory globs.  Ownership and completion fields
    are different contracts and must identify one concrete file each.
    """
    for path in paths:
        normalized = path.replace("\\", "/").strip()
        if (
            not normalized
            or normalized.endswith("/")
            or any(token in normalized for token in ("*", "?", "[", "]"))
        ):
            raise ValueError(
                f"{field} 必须使用具体文件路径: {path!r}；"
                "目录或 glob 只能放在 allowed_paths/allowed_roots"
            )


def _raw_layer_mapping(value: Any, name: str) -> dict[str, tuple[str, ...]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Implementation Contract.{name} 必须是对象")
    result: dict[str, tuple[str, ...]] = {}
    for layer, values in value.items():
        result[str(layer).strip()] = _strings(values, f"{name}.{layer}", allow_empty=True)
    return result


def _normalize_layer_mapping(
    value: dict[str, tuple[str, ...]],
    layers: tuple[str, ...],
    name: str,
    *,
    allow_missing: bool = False,
) -> dict[str, tuple[str, ...]]:
    if not layers:
        if value:
            raise ValueError(f"Implementation Contract.{name} 声明了层映射但缺少 layers")
        return {}
    unknown = set(value) - set(layers)
    if unknown:
        raise ValueError(f"Implementation Contract.{name} 包含未声明层: {', '.join(sorted(unknown))}")
    normalized: dict[str, tuple[str, ...]] = {}
    for layer in layers:
        if layer not in value:
            if allow_missing:
                normalized[layer] = ()
                continue
            raise ValueError(f"Implementation Contract.{name} 缺少层: {layer}")
        normalized[layer] = tuple(value[layer])
    return normalized


def _paths_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = left.removesuffix("**").removesuffix("*")
    right_prefix = right.removesuffix("**").removesuffix("*")
    return bool(left_prefix and right_prefix and (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)))


def _scopes_are_file_disjoint(
    current: ImplementationUnit,
    previous_unit_id: str,
    units: tuple[ImplementationUnit, ...],
) -> bool:
    """Return true when broad directory grants still have disjoint file owners.

    Directory grants are useful for auxiliary files, but they must not weaken
    complete-file ownership.  If both units enumerate concrete ``owned_files``
    and those sets are disjoint, the compiler can safely run them in parallel;
    a unit without an explicit ownership set keeps the conservative overlap
    rejection.
    """
    previous = next((item for item in units if item.unit_id == previous_unit_id), None)
    if previous is None or not current.owned_files or not previous.owned_files:
        return False
    return set(current.owned_files).isdisjoint(previous.owned_files)


def _matches_path(path: str, pattern: str) -> bool:
    import fnmatch
    directory = pattern.endswith("/")
    normalized = pattern.rstrip("/")
    candidates = (path, f"workspace/{path}")
    return any(
        fnmatch.fnmatch(candidate, normalized)
        or fnmatch.fnmatch(candidate, normalized.replace("**", "*"))
        or (directory and candidate.startswith(normalized + "/"))
        for candidate in candidates
    )
