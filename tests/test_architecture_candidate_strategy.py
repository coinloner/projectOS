from app.architecture_execution_config import ArchitectureExecutionConfig
from app.domain.architecture.candidate_policy import ArchitectureCandidatePolicy


def test_all_or_nothing_rejects_partial_candidate_and_default_is_safe():
    config = ArchitectureExecutionConfig(scheme="D")
    policy = ArchitectureCandidatePolicy(config)
    assessment = policy.assess(("a", "b"), ("a",))
    assert assessment.ready_for_quality_gate is False
    assert assessment.publication_allowed is False
    assert assessment.retention_decision == "discard_partial_candidate_from_publication"
    assert config.candidate_strategy == "all_or_nothing"


def test_incremental_strategy_retains_completed_siblings_but_never_publishes_partial():
    config = ArchitectureExecutionConfig(
        scheme="D", candidate_strategy="incremental_candidate"
    )
    policy = ArchitectureCandidatePolicy(config)
    assessment = policy.assess(("a", "b"), ("a",))
    assert assessment.publication_allowed is False
    assert assessment.retention_decision == "retain_completed_siblings_for_local_recovery"
    assert config.retains_partial_candidates is True


def test_complete_candidate_can_pass_quality_gate_in_both_modes():
    for strategy in ("all_or_nothing", "incremental_candidate"):
        policy = ArchitectureCandidatePolicy(
            ArchitectureExecutionConfig(scheme="D", candidate_strategy=strategy)
        )
        assessment = policy.assert_publishable(("a", "b"), ("a", "b"))
        assert assessment.publication_allowed is True
        assert assessment.missing_refs == ()
