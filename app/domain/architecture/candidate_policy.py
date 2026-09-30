"""Deterministic policy for architecture candidate retention and publication.

The LLM may produce module designs, but it never decides whether an incomplete
set is a formal Architecture Contract.  This module keeps that decision in the
control plane and makes the two recovery strategies explicit.
"""

from dataclasses import dataclass
from typing import Iterable

from app.architecture_execution_config import ArchitectureExecutionConfig


@dataclass(frozen=True)
class CandidateAssessment:
    strategy: str
    expected_refs: tuple[str, ...]
    available_refs: tuple[str, ...]
    missing_refs: tuple[str, ...]
    ready_for_quality_gate: bool
    publication_allowed: bool
    retention_decision: str


class ArchitectureCandidatePolicy:
    """Apply candidate strategy without allowing partial publication."""

    def __init__(self, config: ArchitectureExecutionConfig) -> None:
        self._config = config

    @property
    def strategy(self) -> str:
        return self._config.candidate_strategy

    def assess(
        self,
        expected_refs: Iterable[str],
        available_refs: Iterable[str],
    ) -> CandidateAssessment:
        expected = tuple(expected_refs)
        available = tuple(available_refs)
        missing = tuple(ref for ref in expected if ref not in available)
        ready = not missing and bool(expected)
        # Incremental mode keeps completed sibling staged outputs as recovery
        # inputs. all_or_nothing does not reuse a partial set as a candidate;
        # both modes still require a complete set before formal publication.
        retention = (
            "retain_completed_siblings_for_local_recovery"
            if self._config.retains_partial_candidates
            else "discard_partial_candidate_from_publication"
        )
        return CandidateAssessment(
            strategy=self.strategy,
            expected_refs=expected,
            available_refs=available,
            missing_refs=missing,
            ready_for_quality_gate=ready,
            publication_allowed=ready and self._config.requires_complete_candidate,
            retention_decision=retention,
        )

    def assert_publishable(
        self,
        expected_refs: Iterable[str],
        available_refs: Iterable[str],
    ) -> CandidateAssessment:
        assessment = self.assess(expected_refs, available_refs)
        if not assessment.publication_allowed:
            missing = ", ".join(assessment.missing_refs) or "no staged architecture outputs"
            raise ValueError(
                f"架构候选策略 {assessment.strategy} 拒绝不完整候选；缺少: {missing}"
            )
        return assessment
