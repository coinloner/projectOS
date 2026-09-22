"""架构一致性验证器。

验证架构设计中的接口声明和消费关系是否符合消费方式约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.orchestration.consumption_type_inference import (
    ConsumptionType,
    ConsumptionTypeInferenceEngine,
)


@dataclass(frozen=True)
class ArchitectureViolation:
    """架构违规记录。"""
    severity: str  # "error" | "warning"
    rule_name: str
    module_id: str
    message: str
    suggestion: str
    interface_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "severity": self.severity,
            "rule_name": self.rule_name,
            "module_id": self.module_id,
            "message": self.message,
            "suggestion": self.suggestion,
            "interface_id": self.interface_id,
        }


class ArchitectureConsistencyValidator:
    """架构一致性验证器。"""

    def __init__(self):
        self.inference_engine = ConsumptionTypeInferenceEngine()

    def validate(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """
        验证架构设计的一致性。

        Args:
            designs: 架构设计列表，每个设计包含:
                - module_id: str
                - provided_interfaces: list[dict]
                - consumed_interfaces: list[dict]
                - tech_stack: list[str]
                - runtime: str

        Returns:
            list[ArchitectureViolation]: 违规列表
        """
        violations = []

        # 规则 1: 所有消费的接口必须存在
        violations.extend(self._check_missing_interfaces(designs))

        # 规则 2: 前端不能提供 import_code 接口
        violations.extend(self._check_frontend_interfaces(designs))

        # 规则 3: 跨进程不能使用 import_code
        violations.extend(self._check_cross_process_imports(designs))

        # 规则 4: 消费方式类型必须匹配
        violations.extend(self._check_consumption_type_mismatch(designs))

        # 规则 5: 没有循环依赖
        violations.extend(self._check_circular_dependencies(designs))

        return violations

    def _check_missing_interfaces(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """检查引用了不存在的接口。"""
        violations = []

        # 收集所有声明的接口
        provided_interfaces = {}
        for design in designs:
            for interface in design.get("provided_interfaces", []):
                interface_id = interface.get("interface_id")
                if interface_id:
                    provided_interfaces[interface_id] = {
                        "provider_module": design["module_id"],
                        "consumption_type": interface.get("consumption_type"),
                    }

        # 检查所有消费的接口
        for design in designs:
            for consumed in design.get("consumed_interfaces", []):
                interface_id = consumed.get("interface_id")
                if not interface_id:
                    continue

                if interface_id not in provided_interfaces:
                    # 分析为什么缺失
                    suggestion = self._suggest_missing_interface_fix(
                        interface_id, design, designs
                    )

                    violations.append(ArchitectureViolation(
                        severity="error",
                        rule_name="interface_must_exist",
                        module_id=design["module_id"],
                        interface_id=interface_id,
                        message=f"模块 '{design['module_id']}' 引用了不存在的接口 '{interface_id}'",
                        suggestion=suggestion,
                    ))

        return violations

    def _suggest_missing_interface_fix(
        self,
        missing_interface_id: str,
        consumer_design: dict[str, Any],
        all_designs: list[dict[str, Any]],
    ) -> str:
        """为缺失的接口提供修复建议。"""
        # 提取提供者模块 ID
        parts = missing_interface_id.split(".")
        if not parts:
            return "检查接口 ID 格式是否正确（应为 module-id.capability-name）"

        provider_module_id = parts[0]

        # 查找提供者模块
        provider_design = None
        for design in all_designs:
            if design["module_id"] == provider_module_id:
                provider_design = design
                break

        if not provider_design:
            return f"模块 '{provider_module_id}' 不存在，请先创建该模块"

        # 推断提供者的消费方式
        inference = self.inference_engine.infer({
            "tech_stack": provider_design.get("tech_stack", []),
            "runtime": provider_design.get("runtime", "unknown"),
            "purpose": provider_design.get("purpose", ""),
        })

        # 判断问题所在
        if inference.consumption_type == ConsumptionType.HTTP_CALL:
            if "ui" in provider_module_id or "frontend" in provider_module_id:
                return (
                    f"模块 '{provider_module_id}' 是前端应用，应该提供 HTTP 服务接口，"
                    f"但当前没有声明。如果 '{consumer_design['module_id']}' 是集成测试，"
                    f"应该使用 'test_target' 或 'runtime_dependency'，而不是 'consumed_interfaces'。"
                )
            else:
                return (
                    f"模块 '{provider_module_id}' 应该声明其提供的 HTTP 接口。"
                    f"在该模块的 provided_interfaces 中添加此接口。"
                )

        elif inference.consumption_type == ConsumptionType.IMPORT_CODE:
            return (
                f"模块 '{provider_module_id}' 应该声明其提供的代码接口。"
                f"在该模块的 provided_interfaces 中添加此接口。"
            )

        else:
            return (
                f"检查模块 '{provider_module_id}' 是否应该提供接口 '{missing_interface_id}'。"
                f"如果是，请在该模块的 provided_interfaces 中声明。"
            )

    def _check_frontend_interfaces(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """检查前端是否错误地提供了代码接口。"""
        violations = []

        for design in designs:
            # 判断是否为前端模块
            tech_stack = design.get("tech_stack", [])
            runtime = design.get("runtime", "")

            is_frontend = (
                runtime == "browser" or
                any(tech in ["react", "vue", "vite", "webpack"]
                    for tech in [t.lower() for t in tech_stack])
            )

            if not is_frontend:
                continue

            # 检查前端是否提供了 import_code 接口
            for interface in design.get("provided_interfaces", []):
                consumption_type = interface.get("consumption_type")
                if consumption_type == "import_code":
                    violations.append(ArchitectureViolation(
                        severity="error",
                        rule_name="frontend_no_code_interface",
                        module_id=design["module_id"],
                        interface_id=interface.get("interface_id"),
                        message=(
                            f"前端模块 '{design['module_id']}' 不能提供 import_code 接口 "
                            f"'{interface.get('interface_id')}'"
                        ),
                        suggestion=(
                            "前端运行在浏览器（独立进程），不能被服务器代码直接 import。"
                            "应该将 consumption_type 改为 'http_call'。"
                        ),
                    ))

        return violations

    def _check_cross_process_imports(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """检查跨进程的 import_code 依赖。"""
        violations = []

        # 构建模块运行时映射
        module_runtime = {}
        for design in designs:
            module_runtime[design["module_id"]] = design.get("runtime", "unknown")

        # 检查每个消费关系
        for consumer_design in designs:
            consumer_runtime = consumer_design.get("runtime", "unknown")

            for consumed in consumer_design.get("consumed_interfaces", []):
                interface_id = consumed.get("interface_id")
                if not interface_id:
                    continue

                # 提取提供者模块 ID
                provider_module_id = interface_id.split(".")[0]
                provider_runtime = module_runtime.get(provider_module_id, "unknown")

                # 检查是否为 import_code
                consumption_type = consumed.get("consumption_type")
                if consumption_type != "import_code":
                    continue

                # 检查是否跨进程
                if (consumer_runtime != provider_runtime and
                    consumer_runtime != "unknown" and
                    provider_runtime != "unknown"):
                    violations.append(ArchitectureViolation(
                        severity="error",
                        rule_name="no_cross_process_import",
                        module_id=consumer_design["module_id"],
                        interface_id=interface_id,
                        message=(
                            f"模块 '{consumer_design['module_id']}' (runtime: {consumer_runtime}) "
                            f"不能通过 import_code 消费模块 '{provider_module_id}' (runtime: {provider_runtime})"
                        ),
                        suggestion=(
                            "不同运行时环境的代码不能直接 import。"
                            "应该使用 'http_call' 或 'process_spawn' 进行跨进程通信。"
                        ),
                    ))

        return violations

    def _check_consumption_type_mismatch(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """检查消费方式类型不匹配。"""
        violations = []

        # 收集提供的接口
        provided_map = {}
        for design in designs:
            for interface in design.get("provided_interfaces", []):
                interface_id = interface.get("interface_id")
                if interface_id:
                    provided_map[interface_id] = interface.get("consumption_type")

        # 检查消费接口
        for design in designs:
            for consumed in design.get("consumed_interfaces", []):
                interface_id = consumed.get("interface_id")
                consumer_type = consumed.get("consumption_type")

                if not interface_id or not consumer_type:
                    continue

                provider_type = provided_map.get(interface_id)
                if not provider_type:
                    continue

                if consumer_type != provider_type:
                    violations.append(ArchitectureViolation(
                        severity="error",
                        rule_name="consumption_type_mismatch",
                        module_id=design["module_id"],
                        interface_id=interface_id,
                        message=(
                            f"消费方式不匹配: 模块 '{design['module_id']}' 期望通过 '{consumer_type}' "
                            f"消费接口 '{interface_id}'，但提供者声明为 '{provider_type}'"
                        ),
                        suggestion=(
                            f"统一消费方式为 '{provider_type}'，或者修改提供者的声明。"
                        ),
                    ))

        return violations

    def _check_circular_dependencies(self, designs: list[dict[str, Any]]) -> list[ArchitectureViolation]:
        """检查循环依赖。"""
        violations = []

        # 构建依赖图
        graph = {}
        for design in designs:
            module_id = design["module_id"]
            dependencies = set()

            for consumed in design.get("consumed_interfaces", []):
                interface_id = consumed.get("interface_id")
                if interface_id:
                    provider_id = interface_id.split(".")[0]
                    dependencies.add(provider_id)

            graph[module_id] = dependencies

        # 检测循环
        def find_cycle(node: str, path: list[str], visited: set[str]) -> list[str] | None:
            if node in path:
                # 找到循环
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]

            if node in visited:
                return None

            visited.add(node)
            path.append(node)

            for dependency in graph.get(node, []):
                cycle = find_cycle(dependency, path[:], visited)
                if cycle:
                    return cycle

            return None

        visited = set()
        for module_id in graph:
            if module_id not in visited:
                cycle = find_cycle(module_id, [], visited)
                if cycle:
                    violations.append(ArchitectureViolation(
                        severity="error",
                        rule_name="no_circular_dependency",
                        module_id=cycle[0],
                        message=f"检测到循环依赖: {' → '.join(cycle)}",
                        suggestion=(
                            "引入接口抽象或重新划分模块职责以打破循环。"
                            "常见方法: 提取共同依赖到新模块、使用事件解耦、引入依赖注入。"
                        ),
                    ))
                    break  # 只报告第一个循环

        return violations


def format_violations(violations: list[ArchitectureViolation]) -> str:
    """格式化违规列表为可读文本。"""
    if not violations:
        return "✅ 架构验证通过，没有发现违规。"

    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    lines = []
    lines.append(f"❌ 架构验证发现 {len(errors)} 个错误, {len(warnings)} 个警告:\n")

    if errors:
        lines.append("**错误 (必须修复):**")
        for i, error in enumerate(errors[:10], 1):
            lines.append(f"{i}. [{error.rule_name}] {error.message}")
            lines.append(f"   建议: {error.suggestion}")
            lines.append("")
        if len(errors) > 10:
            lines.append(f"   ... 还有 {len(errors) - 10} 个错误")
        lines.append("")

    if warnings:
        lines.append("**警告 (建议修复):**")
        for i, warning in enumerate(warnings[:5], 1):
            lines.append(f"{i}. [{warning.rule_name}] {warning.message}")
            lines.append(f"   建议: {warning.suggestion}")
            lines.append("")
        if len(warnings) > 5:
            lines.append(f"   ... 还有 {len(warnings) - 5} 个警告")

    return "\n".join(lines)
