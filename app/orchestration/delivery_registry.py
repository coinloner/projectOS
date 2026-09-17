"""统一的交付合同注册表。

交付合同是控制面唯一的事实来源：同一份定义同时驱动 WorkItem 的必需工具、
Runner 的 prompt/allowlist、Artifact 预期和恢复策略。这样工具注册变化不会再
被某个层级的复制逻辑悄悄遗漏。
"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from typing import Iterable

from app.execution_context import ExecutionMode


@dataclass(frozen=True)
class WorkItemDeliveryContract:
    """一个可识别的 WorkItem 交付协议。"""

    kind: str
    agent_id: str
    domain: str
    required_tools: tuple[str, ...]
    expected_tool: str | None
    output_artifact_kind: str | None
    output_slot_pattern: str | None
    validator: str | None
    retry_policy: str


class DeliveryContractRegistry:
    """按生命周期角色推导交付合同，而不是在调用方重复 if/else。"""

    @classmethod
    def contract_for(
        cls,
        *,
        agent_id: str,
        execution_mode: ExecutionMode,
        slot: str | None,
        work_item_id: str | None = None,
        stage_id: str | None,
        publish_target: str | None,
    ) -> WorkItemDeliveryContract | None:
        if agent_id != "architecture_agent":
            return None

        slot_value = slot or ""
        if execution_mode is ExecutionMode.PARTITIONED:
            if slot_value in {"design", "baseline", "api", "data", "frontend"}:
                return cls._architecture(
                    kind="architecture.markdown_package",
                    required_tools=("load_architecture_input", "write_staged_architecture"),
                    expected_tool="write_staged_architecture",
                    output_slot_pattern=slot_value,
                    validator="MarkdownArchitecturePackage",
                )
            if slot_value == "blueprint":
                return cls._architecture(
                    kind="architecture.blueprint",
                    required_tools=("load_architecture_input", "write_architecture_blueprint"),
                    expected_tool="write_architecture_blueprint",
                    output_slot_pattern="blueprint",
                    validator="ArchitectureBlueprint",
                )
            if slot_value == "module" or slot_value.startswith("module-"):
                return cls._architecture(
                    kind="architecture.module_design",
                    required_tools=("load_architecture_input", "write_module_design"),
                    expected_tool="write_module_design",
                    output_slot_pattern="module-*",
                    validator="ModuleDesign",
                )
            if slot_value == "implementation" or slot_value.startswith("implementation-"):
                return cls._architecture(
                    kind="architecture.implementation_design",
                    required_tools=("load_architecture_input", "write_implementation_design"),
                    expected_tool="write_implementation_design",
                    output_slot_pattern="implementation-*",
                    validator="ImplementationDesign",
                )

        if execution_mode is ExecutionMode.INTEGRATION and stage_id == "architecture_markdown_integration":
            return cls._architecture(
                kind="architecture.markdown_integration",
                required_tools=("load_architecture_input", "create_architecture_candidate"),
                expected_tool="create_architecture_candidate",
                output_slot_pattern=None,
                validator="MarkdownArchitecturePackage",
            )

        if execution_mode is ExecutionMode.INTEGRATION and (
            stage_id == "architecture_integration"
            or (stage_id is None and (
                (work_item_id or "").endswith("architecture-layered-integration")
                or publish_target == "architecture"
            ))
        ):
            return cls._architecture(
                kind="architecture.integration",
                required_tools=("load_architecture_input", "integrate_architecture_designs"),
                expected_tool="integrate_architecture_designs",
                output_slot_pattern=None,
                validator="ArchitectureDesignBundle",
            )
        return None

    @staticmethod
    def _architecture(
        *,
        kind: str,
        required_tools: tuple[str, ...],
        expected_tool: str,
        output_slot_pattern: str | None,
        validator: str,
    ) -> WorkItemDeliveryContract:
        return WorkItemDeliveryContract(
            kind=kind,
            agent_id="architecture_agent",
            domain="architecture",
            required_tools=required_tools,
            expected_tool=expected_tool,
            output_artifact_kind=("architecture_markdown" if validator == "MarkdownArchitecturePackage" else "architecture_design"),
            output_slot_pattern=output_slot_pattern,
            validator=validator,
            retry_policy="artifact_schema_repair",
        )

    @classmethod
    def required_tools_for(cls, **kwargs: object) -> tuple[str, ...]:
        contract = cls.contract_for(**kwargs)  # type: ignore[arg-type]
        return contract.required_tools if contract is not None else ()

    @classmethod
    def expected_tool_for(cls, **kwargs: object) -> str | None:
        contract = cls.contract_for(**kwargs)  # type: ignore[arg-type]
        return contract.expected_tool if contract is not None else None

    @classmethod
    def allowlist_for(cls, **kwargs: object) -> tuple[str, ...]:
        contract = cls.contract_for(**kwargs)  # type: ignore[arg-type]
        return contract.required_tools if contract is not None else ()
    @classmethod
    def validate_work_item(cls, item: object) -> tuple[str, ...]:
        """Validate the structural part of a delivery contract before execution.

        This check deliberately does not inspect the ToolGateway: registration and
        visibility are runtime concerns. It catches malformed plans early, while
        ``ToolGateway.preflight`` catches environment/allowlist drift immediately
        before the Agent is created.
        """
        contract = cls.contract_for(
            agent_id=str(getattr(item, "agent_id", "")),
            execution_mode=getattr(item, "execution_mode"),
            slot=getattr(item, "slot", None),
            work_item_id=getattr(item, "id", None),
            stage_id=getattr(item, "stage_id", None),
            publish_target=getattr(item, "publish_target", None),
        )
        if contract is None:
            return ()

        errors: list[str] = []
        required_tools = tuple(getattr(item, "required_tools", ()) or ())
        if required_tools != contract.required_tools:
            errors.append(
                f"required_tools={list(required_tools)!r} 与合同要求 "
                f"{list(contract.required_tools)!r} 不一致"
            )

        if contract.output_slot_pattern is not None:
            slot = str(getattr(item, "slot", "") or "")
            if not fnmatch.fnmatch(slot, contract.output_slot_pattern):
                errors.append(
                    f"slot={slot!r} 不匹配 {contract.output_slot_pattern!r}"
                )

        return tuple(errors)

    @classmethod
    def validate_plan(cls, items: Iterable[object]) -> tuple[str, ...]:
        """Validate every WorkItem and enforce unique staged output slots."""
        errors: list[str] = []
        seen_slots: dict[tuple[str, str, str], str] = {}
        for item in items:
            item_id = str(getattr(item, "id", "<unknown>"))
            errors.extend(f"{item_id}: {error}" for error in cls.validate_work_item(item))
            if getattr(item, "execution_mode", None) is ExecutionMode.PARTITIONED:
                artifact_key = str(getattr(item, "artifact_key", "") or "")
                slot = str(getattr(item, "slot", "") or "")
                if artifact_key and slot:
                    # ``slot`` is a semantic partition (for example ``backend``),
                    # not a physical output identity. Multi-file units may share it
                    # safely; ownership is unique at the concrete file boundary.
                    owned_files = tuple(getattr(item, "owned_files", ()) or ())
                    identities = owned_files or (slot,)
                    for identity in identities:
                        key = (artifact_key, str(identity), str(getattr(item, "stage_id", "") or ""))
                        previous = seen_slots.get(key)
                        if previous is not None and previous != item_id:
                            errors.append(
                                f"staged output {artifact_key}:{identity}:{key[2] or '-'} "
                                f"被 {previous} 和 {item_id} 重复占用"
                            )
                        else:
                            seen_slots[key] = item_id
        return tuple(errors)

    @staticmethod
    def validate_content(
        contract: WorkItemDeliveryContract, content: str
    ) -> object:
        """Run the validator named by a delivery contract.

        Validator resolution lives here so Planner, Runner and recovery cannot
        silently choose different schemas for the same WorkItem kind.
        """
        if contract.domain != "architecture" or contract.validator is None:
            return content
        if contract.validator == "MarkdownArchitecturePackage":
            if not content.strip():
                raise ValueError("Architecture Markdown package must not be empty")
            return content
        from app.domain.architecture.design_contract import (
            ArchitectureBlueprint,
            ImplementationDesign,
            ModuleDesign,
            parse_design,
        )

        parsed = parse_design(content)
        expected = {
            "ArchitectureBlueprint": ArchitectureBlueprint,
            "ModuleDesign": ModuleDesign,
            "ImplementationDesign": ImplementationDesign,
        }.get(contract.validator)
        if expected is None:
            raise ValueError(f"未知交付 validator: {contract.validator}")
        if not isinstance(parsed, expected):
            raise ValueError(
                f"交付对象类型不匹配: expected={contract.validator}, "
                f"actual={type(parsed).__name__}"
            )
        return parsed

