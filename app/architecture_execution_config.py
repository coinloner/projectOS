"""Immutable Architecture-only execution policy; never overrides global runtime settings."""
from dataclasses import asdict, dataclass
from typing import Literal
from hashlib import sha256
import json


@dataclass(frozen=True)
class ArchitectureExecutionConfig:
    # Named profiles keep prompt, tools and recovery policy coherent.
    mode: str = "baseline"
    schema_version: int = 1
    # Direct domain-service contexts remain legacy unless the control plane
    # explicitly selects D; GraphRunner does that for the production path.
    scheme: Literal["D", "legacy"] = "legacy"
    # Controls whether incomplete architecture candidates are discarded as a
    # single unit or retained for bounded incremental recovery. This is a
    # control-plane policy; neither mode permits publication before the final
    # quality gate has proved the complete bundle.
    candidate_strategy: Literal["all_or_nothing", "incremental_candidate"] = "all_or_nothing"

    def __post_init__(self):
        if self.scheme not in ("D", "legacy"):
            raise ValueError("Unsupported Architecture scheme")
        if self.mode not in ("baseline", "checkpointed"):
            raise ValueError("Unsupported Architecture execution mode")
        if self.schema_version != 1:
            raise ValueError("Unsupported Architecture config schema")
        if self.candidate_strategy not in ("all_or_nothing", "incremental_candidate"):
            raise ValueError("Unsupported Architecture candidate strategy")

    @property
    def recursive_enabled(self) -> bool:
        return self.scheme == "D"

    @property
    def checkpoints_enabled(self) -> bool:
        return self.mode == "checkpointed"

    @property
    def retains_partial_candidates(self) -> bool:
        """Whether completed sibling design outputs survive a module failure."""
        return self.candidate_strategy == "incremental_candidate"

    @property
    def requires_complete_candidate(self) -> bool:
        """Both strategies require a complete bundle before formal publication."""
        return True

    def supports_checkpoints(self, agent_id, execution_mode, slot) -> bool:
        return (self.checkpoints_enabled and agent_id == "architecture_agent"
                and execution_mode == "partitioned"
                and (slot or "").startswith(("module-", "implementation-")))

    @property
    def intermediate_tools(self) -> tuple[str, ...]:
        return (("load_architecture_intermediate", "write_architecture_intermediate")
                if self.checkpoints_enabled else ())

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def digest(self) -> str:
        return sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
