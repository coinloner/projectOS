"""确定性生成实现单元之间的 Python import binding。

Architecture/LLM 只负责声明文件 ownership 和导出符号；模块路径不能由
CodeAgent 根据 interface id 猜测。本模块把 workspace 相对路径转换为唯一的
Python module 名称，并提供受控的符号规范化，供 Contract Compiler 和
Integration 复用。
"""

from __future__ import annotations

import ast
import re
from typing import Iterable


_PYTHON_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYTHON_DOTTED_NAME = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$"
)


def normalize_workspace_path(path: str) -> str:
    """Return a normalized workspace-relative path."""
    return path.replace("\\", "/").removeprefix("workspace/").lstrip("/")


def python_module_for_path(path: str) -> str | None:
    """Derive the canonical import module for a Python workspace file.

    The generated projects use ``workspace/backend`` as the Python project
    root.  Therefore ``backend/app/task_service.py`` canonically maps to
    ``app.task_service``.  We intentionally do not infer modules for frontend,
    docs, Docker or other non-Python files.
    """
    normalized = normalize_workspace_path(path)
    if not normalized.endswith(".py"):
        return None
    relative = normalized.removeprefix("backend/")
    parts = relative[:-3].split("/")
    if parts and parts[-1] == "__init__":
        parts.pop()
    parts = [part for part in parts if part]
    if not parts or any(not _PYTHON_NAME.fullmatch(part) for part in parts):
        return None
    return ".".join(parts)


def normalize_python_symbols(values: Iterable[str] | None) -> tuple[str, ...]:
    """Keep only concrete Python names, dropping prose/interface ids.

    A dotted value is retained only when it starts with an upper-case name;
    this supports class-member declarations such as ``TaskService.create``
    without treating an interface id such as ``task_management.task_api`` as a
    Python symbol.
    """
    normalized: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if not value or not _PYTHON_DOTTED_NAME.fullmatch(value):
            continue
        base = value.removesuffix("()")
        parts = base.split(".")
        if len(parts) > 1 and not parts[0][:1].isupper():
            continue
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def canonical_binding(
    *, unit_id: str, owned_file: str, provided_symbols: Iterable[str] | None = None
) -> dict[str, object] | None:
    """Build a serializable binding for one owned file, if it is Python."""
    module = python_module_for_path(owned_file)
    if module is None:
        return None
    return {
        "unit_id": unit_id,
        "owned_file": normalize_workspace_path(owned_file),
        "language": "python",
        "module": module,
        "provided_symbols": list(normalize_python_symbols(provided_symbols)),
    }


def python_symbols_in_source(source: str, *, filename: str = "<source>") -> frozenset[str]:
    """Return names that a Python module exposes at module scope.

    This is deliberately a small, syntax-only export model.  It is used by
    the integration gate to answer the concrete question "does the producer
    file contain the symbol that the consumer imports?"; it never executes
    generated code or imports the generated application.
    """
    tree = ast.parse(source, filename=filename)
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                symbols.add(alias.asname or alias.name.split(".", 1)[0])
    return frozenset(symbols)
