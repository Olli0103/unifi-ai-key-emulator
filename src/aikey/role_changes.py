"""Non-secret, non-activating consequences of one role's proposed model."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleModelPreview:
    current_revision: str
    resulting_revision: str
    role: str
    previous_model: str | None
    proposed_model: str
    restart_required: bool
    index_effect: str = "unchanged"
    capability_tested: bool = False

    @property
    def job_effect(self) -> str:
        return "current_runtime_until_restart" if self.restart_required else "unchanged"
