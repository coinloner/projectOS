"""Episodic 摘要的确定性质量校验。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryQuality:
    valid: bool
    issues: tuple[str, ...] = ()


def validate_summary(
    *,
    content: str,
    status: str,
    source_refs: tuple[str, ...],
    available_event_ids: set[str],
) -> SummaryQuality:
    issues: list[str] = []
    if not content.strip():
        issues.append("摘要内容不能为空")
    if not status.strip():
        issues.append("摘要必须包含 Trace 状态")
    if not source_refs:
        issues.append("摘要必须引用至少一个原始事件")
    missing = sorted(set(source_refs) - available_event_ids)
    if missing:
        issues.append("摘要引用了不存在的事件: " + ", ".join(missing))
    if "Trace 运行摘要" not in content:
        issues.append("摘要缺少固定标题")
    if f"status={status}" not in content:
        issues.append("摘要状态与正文不一致")
    return SummaryQuality(valid=not issues, issues=tuple(issues))
