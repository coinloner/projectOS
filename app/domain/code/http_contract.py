"""Check statically declared HTTP consumers against their owned FastAPI API.

This is an integration guard, not a replacement for API or browser E2E. It
checks only calls whose method and path can be resolved without executing
untrusted generated code. The Project Contract selects the provider and its
consumers; no application-specific route convention is inferred here.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING

from app.domain.architecture.implementation_contract import HttpOperationContract

if TYPE_CHECKING:
    from app.domain.architecture.implementation_contract import ImplementationContract


_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_JS_CONST = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(['\"])(/[^'\"\r\n]*)\2")
_JS_TEMPLATE_PART = re.compile(r"\$\{([^{}]*)\}")
_JS_CALL = re.compile(r"\b(?:request|fetch)\s*\(")
_JS_METHOD = re.compile(r"\bmethod\s*:\s*(['\"])([A-Za-z]+)\1")
_VARIABLE = re.compile(r"^\{[^{}]+\}$")


@dataclass(frozen=True)
class HttpOperation:
    method: str
    path: str
    line: int
    parameter_types: tuple[tuple[str, str], ...] = ()


def _path(value: str) -> str:
    """Strip a query/fragment; retain path placeholders for route matching."""
    return value.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"


def _python_path(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else "{parameter}" if isinstance(part, ast.FormattedValue)
            else ""
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _python_path(node.left, constants)
        right = _python_path(node.right, constants)
        # A known prefix with an unresolved query expression is still an
        # exact route path. Never guess an unknown path suffix.
        if left is not None and right is not None:
            return left + right
        if left is not None and "?" in left:
            return left
        return None
    if isinstance(node, ast.IfExp):
        first = _python_path(node.body, constants)
        second = _python_path(node.orelse, constants)
        if first == second:
            return first
        if first is not None and second is not None and _path(first) == _path(second):
            return first
    return None


def _fastapi_routes(source: str, filename: str) -> tuple[HttpOperation, ...]:
    tree = ast.parse(source, filename=filename)
    constants: dict[str, str] = {}
    routers: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if not isinstance(target, ast.Name) or value is None:
                    continue
                string = _python_path(value, constants)
                if string is not None:
                    constants[target.id] = string
                if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                        and value.func.id == "APIRouter"):
                    prefix = next((keyword.value for keyword in value.keywords if keyword.arg == "prefix"), None)
                    routers[target.id] = _python_path(prefix, constants) if prefix is not None else ""
    routes: list[HttpOperation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        annotations = {
            argument.arg: argument.annotation.id
            for argument in (*node.args.posonlyargs, *node.args.args)
            if isinstance(argument.annotation, ast.Name)
        }
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.args):
                continue
            method = decorator.func.attr.upper()
            if method not in _METHODS:
                continue
            prefix = routers.get(decorator.func.value.id, "")
            raw = _python_path(decorator.args[0], constants)
            if raw is None or not raw.startswith("/"):
                continue
            route = _path(prefix + raw)
            variables = {segment[1:-1] for segment in route.split("/") if _VARIABLE.fullmatch(segment)}
            routes.append(HttpOperation(method, route, node.lineno,
                                        tuple((name, annotations.get(name, "str")) for name in sorted(variables))))
    return tuple(routes)


def _python_calls(source: str, filename: str) -> tuple[HttpOperation, ...]:
    tree = ast.parse(source, filename=filename)
    constants: dict[str, str] = {}
    calls: list[HttpOperation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    string = _python_path(value, constants)
                    if string is not None:
                        constants[target.id] = string
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method: str | None = None
        route: str | None = None
        if len(node.args) >= 3 and isinstance(node.args[1], ast.Constant):
            # A base-URL helper, e.g. _ok(base, "GET", "/items/{id}").
            candidate = node.args[1].value
            if isinstance(candidate, str) and candidate.upper() in _METHODS:
                method = candidate.upper()
                route = _python_path(node.args[2], constants)
        elif isinstance(node.func, ast.Attribute):
            candidate = node.func.attr.upper()
            if candidate in _METHODS and node.args:
                method = candidate
                route = _python_path(node.args[0], constants)
        if method and route and route.startswith("/"):
            calls.append(HttpOperation(method, _path(route), node.lineno))
    return tuple(calls)


def _js_arguments(source: str, start: int) -> str:
    """Return the balanced call body; ignore brackets inside JS strings/templates."""
    depth = 1
    quote: str | None = None
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start:index]
    return ""


def _js_calls(source: str) -> tuple[HttpOperation, ...]:
    constants = {name: value for name, _, value in _JS_CONST.findall(source)}
    operations: list[HttpOperation] = []
    for match in _JS_CALL.finditer(source):
        body = _js_arguments(source, match.end())
        first = body.lstrip()
        if not first:
            continue
        if first[0] in "'\"`":
            end = first.find(first[0], 1)
            if end < 0:
                continue
            value = first[1:end]
        else:
            name = re.match(r"[A-Za-z_$][\w$]*", first)
            if name is None:
                continue
            value = constants.get(name.group())
            if value is None:
                continue
        def interpolate(part: re.Match[str]) -> str:
            expression = part.group(1).strip()
            if expression in constants:
                return constants[expression]
            if expression in {"query", "search", "searchParams"}:
                return "" if part.end() == len(value) else "{parameter}"
            return "{parameter}"
        value = _JS_TEMPLATE_PART.sub(interpolate, value)
        if not value.startswith("/"):
            continue
        method_match = _JS_METHOD.search(body)
        method = method_match.group(2).upper() if method_match else "GET"
        if method in _METHODS:
            operations.append(HttpOperation(method, _path(value), source.count("\n", 0, match.start()) + 1))
    return tuple(operations)


def _route_matches(call: HttpOperation, route: HttpOperation) -> bool:
    if call.method != route.method:
        return False
    called = call.path.split("/")
    provided = route.path.split("/")
    if len(called) != len(provided):
        return False
    types = dict(getattr(route, "parameter_types", ()))
    for actual, expected in zip(called, provided):
        if _VARIABLE.fullmatch(expected):
            if _VARIABLE.fullmatch(actual):
                continue
            annotation = types.get(expected[1:-1], "str")
            if annotation == "int" and not actual.isdecimal():
                return False
            continue
        if actual != expected:
            return False
    return True


def validate_http_consumers(root: Path, contract: "ImplementationContract") -> tuple[str, ...]:
    """Return attributable route drift in existing, contract-owned files.

    A provider not yet integrated is not inspected in an earlier wave. A
    consumer without statically resolvable calls remains subject to runtime
    E2E; this check never represents an unsupported language as verified.
    """
    issues: list[str] = []
    for interface in contract.interfaces:
        if interface.kind != "api" or not interface.owner_file:
            continue
        provider_path = interface.owner_file.removeprefix("workspace/")
        provider = root / provider_path
        if not provider.is_file() or provider.suffix != ".py":
            continue
        source = provider.read_text(encoding="utf-8")
        if "FastAPI(" not in source and "APIRouter(" not in source:
            continue
        try:
            routes = _fastapi_routes(source, str(provider))
        except SyntaxError:
            continue  # Python syntax is checked by the existing code gate.
        declared = tuple(
            HttpOperationContract(operation.method, operation.path)
            for operation in getattr(interface, "operations", ())
        )
        if declared:
            missing_declared = [
                operation for operation in declared
                if not any(_route_matches(operation, route) for route in routes)
            ]
            for operation in missing_declared:
                issues.append(
                    f"{provider_path} 未实现 API 合同 {interface.interface_id} "
                    f"声明的 {operation.method} {operation.path}"
                )
            expected_routes = declared
        else:
            expected_routes = routes
        for unit in contract.units:
            if interface.interface_id not in unit.consumes_interfaces:
                continue
            for owned in unit.owned_files:
                path = owned.removeprefix("workspace/")
                consumer = root / path
                if not consumer.is_file():
                    continue
                text = consumer.read_text(encoding="utf-8")
                if consumer.suffix == ".py":
                    try:
                        calls = _python_calls(text, str(consumer))
                    except SyntaxError:
                        continue
                elif consumer.suffix in {".js", ".jsx", ".ts", ".tsx"}:
                    calls = _js_calls(text)
                else:
                    continue
                for call in calls:
                    if not any(_route_matches(call, route) for route in expected_routes):
                        suggestions = ", ".join(f"{route.method} {route.path}" for route in expected_routes[:20])
                        issues.append(
                            f"{path}:{call.line} 消费接口 {interface.interface_id} 使用 "
                            f"{call.method} {call.path}，provider={provider_path} 未提供匹配路由"
                            f"（可用: {suggestions or '无静态路由'}）"
                        )
    return tuple(dict.fromkeys(issues))
