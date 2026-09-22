"""Immutable Architecture-only execution policy; never overrides global runtime settings."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class ArchitectureExecutionConfig:
    # Named profiles keep prompt, tools and recovery policy coherent.
    mode: str = "baseline"
    schema_version: int = 1

    def __post_init__(self):
        if self.mode not in ("baseline", "checkpointed"):
            raise ValueError("Unsupported Architecture execution mode")
        if self.schema_version != 1:
            raise ValueError("Unsupported Architecture config schema")

    @property
    def checkpoints_enabled(self) -> bool:
        return self.mode == "checkpointed"

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
