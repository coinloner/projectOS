"""代码集成节点：LLM 审核，确定性合并。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from app.agent.base_agent import BaseAgent
from app.agent.result import AgentResult
from app.domain.code.service import CodeIntegrationService
from app.execution_context import ExecutionContext, ExecutionMode
from app.json_transport import strip_json_transport_noise
from app.tool_manager.gateway import ToolGateway


@dataclass(frozen=True)
class IntegrationFinding:
    code: str
    severity: str
    summary: str
    stage: str = "integration"
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "stage": self.stage,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class IntegrationReview:
    """LLM 只能提交这份合并决策，不包含代码或补丁。"""

    verdict: str
    rationale: str
    findings: tuple[IntegrationFinding, ...] = ()
    adapter_requests: tuple[str, ...] = ()

    @classmethod
    def parse(cls, content: str) -> "IntegrationReview":
        try:
            payload = json.loads(strip_json_transport_noise(content))
        except json.JSONDecodeError as error:
            raise ValueError(f"Integration Review 必须是 JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("Integration Review 必须是 JSON 对象")
        if {"code", "patch", "content", "files_to_write"}.intersection(payload):
            raise ValueError("Integration Review 不得包含代码、补丁或写文件指令")
        verdict = str(payload.get("verdict", "")).strip().lower()
        if verdict not in {"approve", "reject", "needs_adapter"}:
            raise ValueError("Integration Review.verdict 必须是 approve、reject 或 needs_adapter")
        rationale = str(payload.get("rationale", "")).strip()
        if not rationale:
            raise ValueError("Integration Review.rationale 不能为空")
        findings = _findings(payload.get("findings", []))
        raw_adapter_requests = payload.get("adapter_requests", [])
        if raw_adapter_requests is None and verdict != "needs_adapter":
            raw_adapter_requests = []
        adapter_requests = _strings(raw_adapter_requests, "adapter_requests", allow_empty=True)
        if verdict == "needs_adapter" and not adapter_requests:
            raise ValueError("needs_adapter 必须列出 adapter_requests")
        return cls(verdict, rationale, findings, adapter_requests)


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or (not allow_empty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Integration Review.{field} 必须是非空字符串数组")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _findings(value: Any) -> tuple[IntegrationFinding, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("Integration Review.findings 必须是数组")
    findings: list[IntegrationFinding] = []
    for index, item in enumerate(value, 1):
        if isinstance(item, str) and item.strip():
            findings.append(IntegrationFinding(f"finding.{index}", "warning", item.strip()))
            continue
        if not isinstance(item, dict):
            raise ValueError("Integration Review.findings 元素必须是字符串或对象")
        severity = str(item.get("severity", "warning")).strip().lower()
        if severity not in {"blocker", "error", "warning", "info"}:
            raise ValueError("Integration Review.findings.severity 无效")
        summary = str(item.get("summary", "")).strip()
        if not summary:
            raise ValueError("Integration Review.findings.summary 不能为空")
        evidence = _strings(item.get("evidence", []), "finding.evidence", allow_empty=True)
        findings.append(IntegrationFinding(
            code=str(item.get("code", f"finding.{index}")).strip() or f"finding.{index}",
            severity=severity,
            summary=summary,
            stage=str(item.get("stage", "integration")).strip() or "integration",
            evidence=evidence,
        ))
    return tuple(findings)


def _has_merge_blocking_finding(review: IntegrationReview) -> bool:
    # Only an explicit structured blocker can veto this stage. Deterministic
    # GitCodeIntegrationPolicy remains the authority for actual path/conflict
    # violations; natural-language keywords are never used as a gate.
    return any(item.severity == "blocker" for item in review.findings)


class CodeIntegrationAgent:
    """只审核并合并已有 ChangeSet，绝不直接生成或写入业务代码。"""

    def __init__(self, gateway: ToolGateway, service: CodeIntegrationService, *, reviewer: Any | None = None) -> None:
        self._service = service
        self._reviewer = reviewer or BaseAgent(
            gateway=gateway,
            domain="code_integration",
            role="代码集成审查员",
            goal="依据项目质量合同审查已有 ChangeSet，并决定是否允许合并",
            backstory=_BACKSTORY,
            max_iterations=3,
        )

    def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
        if context is None or context.execution_mode is not ExecutionMode.INTEGRATION:
            raise RuntimeError("CodeIntegrationAgent 只能运行在 INTEGRATION WorkItem")
        evidence = self._service.review_evidence(context)
        prompt = (
            "你只能审核已有 ChangeSet，不能编写、修改或输出任何代码。\n"
            "请严格返回 JSON：{\"verdict\":\"approve|reject|needs_adapter\","
            "\"rationale\":\"...\",\"findings\":[{\"code\":\"...\",\"severity\":\"blocker|error|warning|info\",\"summary\":\"...\",\"stage\":\"integration|preflight|tests|review\",\"evidence\":[]}],\"adapter_requests\":[]}。\n"
            "approve 表示允许控制面合并已有 ChangeSet；reject 表示证据或合同不满足；"
            "needs_adapter 只能描述需要已有实现单元提供的适配文件路径，不能生成适配代码。\n"
            "固定底线：不得越权路径、不得新增业务功能、不得以自然语言代替文件或测试证据。\n"
            "阶段边界：本节点发生在 tests 和 review 之前。只判断当前已有 ChangeSet 是否可以安全合并；"
            "不要因为 environment.md、implementation.md、tests.md、review.md 或真实运行证据尚未由后续节点产出而拒绝。"
            "下游证据完整性由 tests/review 节点负责。运行时入口、Dockerfile、前后端字段契约、"
            "迁移脚本和依赖完整性属于后续可运行性验证：可以作为 findings，但不得仅因此拒绝 Git 合并；"
            "只有确定性 Policy 违反、越权路径、重复文件、缺失 ChangeSet、Git 冲突或无法解析的变更才可 reject。\n"
            f"项目任务：{task}\n只读 ChangeSet 证据：{evidence}"
        )
        result = self._reviewer.run(prompt, context=context)
        if result.capability_request is not None:
            raise RuntimeError("Integration Review 不允许请求外部能力")
        review = IntegrationReview.parse(result.content or "")
        if review.verdict == "reject" and not _has_merge_blocking_finding(review):
            review = IntegrationReview(
                verdict="approve",
                rationale="集成阶段记录运行时风险，交由后续 tests/review 验证：" + review.rationale,
                findings=review.findings,
                adapter_requests=review.adapter_requests,
            )
        if review.verdict == "needs_adapter":
            evidence_payload = json.loads(evidence)
            available = {
                str(path).removeprefix("workspace/")
                for change in evidence_payload.get("changesets", [])
                if isinstance(change, dict)
                for path in change.get("changed_files", [])
            }
            missing = [path for path in review.adapter_requests if path not in available]
            if missing:
                raise RuntimeError(
                    "Integration Review 需要适配 ChangeSet，但适配文件尚未由实现节点提供: "
                    + ", ".join(missing)
                )
            # The adapter already exists in an authorized ChangeSet.  The
            # integration node only connects/merges it; it never authors code.
            review = IntegrationReview(
                verdict="approve",
                rationale=review.rationale,
                findings=review.findings,
                adapter_requests=review.adapter_requests,
            )
        self._service.record_review(context, review)
        summary = self._service.integrate(context, review=review)
        return AgentResult.completed(summary + "\nLLM Integration Review: " + review.rationale)


_BACKSTORY = """\
你是代码集成审查员，不是开发者。
你只能读取 ChangeSet、路径、变更摘要和项目质量合同，判断已有实现是否可以合并。
你不能调用写文件工具，不能输出代码、补丁、函数实现或新的业务逻辑。
如果需要适配，只能列出应由已有实现单元提供的适配文件路径，并返回 needs_adapter。
最终合并由控制面 Git 服务和确定性 Policy 执行。"""
