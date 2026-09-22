"""Shared, bounded Agent progress reporting tool."""

from __future__ import annotations

import json

from app.execution_context import ExecutionContext
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef


_WORK_STAGES = ("analysis", "input_review", "implementation", "validation", "integration")


def report_progress(
    context: ExecutionContext,
    work_stage: str,
    summary: str,
    next_action: str,
    confirmed_refs: list[str] | None = None,
) -> str:
    """Accept a short working summary without treating it as a durable fact."""
    if work_stage not in _WORK_STAGES:
        raise ValueError(f"work_stage 必须是 {', '.join(_WORK_STAGES)} 之一")
    summary = " ".join(str(summary).split()).strip()
    next_action = " ".join(str(next_action).split()).strip()
    if not summary or len(summary) > 240:
        raise ValueError("summary 必须为 1 到 240 个字符")
    if not next_action or len(next_action) > 240:
        raise ValueError("next_action 必须为 1 到 240 个字符")
    refs = tuple(dict.fromkeys(str(value).strip() for value in (confirmed_refs or []) if str(value).strip()))
    authorized = {ref.ref_id for ref in context.input_refs}
    unknown = sorted(set(refs) - authorized)
    if unknown:
        raise PermissionError("confirmed_refs 包含当前 WorkItem 未授权引用: " + ", ".join(unknown))
    progress = context.progress
    if progress is None or not hasattr(progress, "report_semantic"):
        return json.dumps({"ok": True, "accepted": False, "reason": "progress_disabled"}, ensure_ascii=False)
    accepted = progress.report_semantic(
        work_stage=work_stage,
        summary=summary,
        next_action=next_action,
        confirmed_refs=refs,
    )
    return json.dumps(
        {
            "ok": True,
            "accepted": accepted,
            "reason": None if accepted else "duplicate_progress",
        },
        ensure_ascii=False,
    )


def register_progress_tools(gateway: ToolGateway, domains: tuple[str, ...]) -> None:
    """Install one control-plane progress tool in every Agent domain."""
    definition = ToolDef(
        name="report_progress",
        description=(
            "报告简短、可公开的工作状态，不要包含内部推理、prompt、源码正文或工具参数。"
            "该工具只更新可观测状态，不能宣告节点完成，也不能创建事实。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "work_stage": {
                    "type": "string",
                    "enum": list(_WORK_STAGES),
                    "description": "当前工作的稳定阶段分类",
                },
                "summary": {"type": "string", "description": "最多 240 字的已完成动作摘要"},
                "next_action": {"type": "string", "description": "最多 240 字的下一步动作"},
                "confirmed_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仅可引用任务输入中明确授权的 ref_id",
                },
            },
            "required": ["work_stage", "summary", "next_action", "confirmed_refs"],
            "additionalProperties": False,
        },
        completion_policy="continue",
    )
    for domain in dict.fromkeys(domains):
        gateway.register_toolset(
            domain=domain,
            name="execution_progress",
            toolset=ExecutionToolSetSource([(definition, report_progress)]),
        )
