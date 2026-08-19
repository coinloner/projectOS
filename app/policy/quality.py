"""节点输出的最小可量化质量规则。"""

from __future__ import annotations

from dataclasses import dataclass

from app.artifact.repository import ArtifactRef
from app.workspace.git_repository import ChangeSet


@dataclass(frozen=True)
class QualityIssue:
    rule_id: str
    summary: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport:
    policy_id: str
    issues: tuple[QualityIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


class GitCodeIntegrationPolicy:
    """Git ChangeSet 版本的代码集成底线策略。

    该策略只验证控制面事实：分区是否齐全、ChangeSet 是否存在、文件是否
    留在分区声明的目录中，以及所有分区是否共享同一个 baseline。业务代码的
    正确性仍由 TestAgent 和后续质量门负责。
    """

    policy_id = "code.git-integration.v1"

    def evaluate(
        self,
        outputs: tuple[tuple[ArtifactRef, ChangeSet], ...],
        *,
        missing_refs: tuple[ArtifactRef, ...] = (),
    ) -> QualityReport:
        issues: list[QualityIssue] = []
        if not outputs:
            issues.append(
                QualityIssue(
                    rule_id="code.changesets_required",
                    summary="代码集成至少需要一个 Git ChangeSet。",
                )
            )

        slots = {ref.slot for ref, _ in outputs}
        for required_slot in ("backend", "frontend"):
            if required_slot not in slots:
                issues.append(
                    QualityIssue(
                        rule_id=f"code.{required_slot}_scope_required",
                        summary=f"缺少 {required_slot} Git ChangeSet。",
                    )
                )

        for ref in missing_refs:
            issues.append(
                QualityIssue(
                    rule_id="code.changeset_available",
                    summary=f"分区 {ref.slot} 没有可用的 Git ChangeSet。",
                    evidence_refs=(ref.ref_id,),
                )
            )

        baselines = {change.base_commit for _, change in outputs}
        if len(baselines) > 1:
            issues.append(
                QualityIssue(
                    rule_id="code.common_baseline_required",
                    summary="所有代码分区必须从同一个 baseline 创建。",
                    evidence_refs=tuple(change.commit for _, change in outputs),
                )
            )

        seen_files: set[str] = set()
        for ref, change in outputs:
            if ref.layer != "staged" or ref.artifact_key != "implementation":
                issues.append(
                    QualityIssue(
                        rule_id="code.invalid_source_ref",
                        summary="Git 代码集成只能使用 implementation 暂存引用。",
                        evidence_refs=(ref.ref_id,),
                    )
                )
                continue
            if not change.changed_files:
                issues.append(
                    QualityIssue(
                        rule_id="code.files_required",
                        summary=f"分区 {ref.slot} 的 ChangeSet 没有文件变更。",
                        evidence_refs=(change.commit,),
                    )
                )
            prefix = f"workspace/{ref.slot}/"
            for path in change.changed_files:
                if not path.startswith(prefix):
                    issues.append(
                        QualityIssue(
                            rule_id="code.scope_boundary",
                            summary=f"文件 {path} 越过 {ref.slot} 分区边界。",
                            evidence_refs=(change.commit,),
                        )
                    )
                if path in seen_files:
                    issues.append(
                        QualityIssue(
                            rule_id="code.duplicate_path",
                            summary=f"多个代码分区声明了同一个文件: {path}。",
                            evidence_refs=(change.commit,),
                        )
                    )
                seen_files.add(path)
        return QualityReport(policy_id=self.policy_id, issues=tuple(issues))
