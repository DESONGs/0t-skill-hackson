from __future__ import annotations

from dataclasses import dataclass

from .models import TeamAdapterCapability, TeamAdapterSpec


@dataclass(frozen=True, slots=True)
class AdapterLaunchEnvelope:
    adapter_id: str
    display_name: str
    handoff_markdown: str

    def as_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "handoff_markdown": self.handoff_markdown,
        }


def build_builtin_adapters() -> dict[str, TeamAdapterSpec]:
    roles = ["planner", "optimizer", "reviewer", "benchmark-runner"]
    return {
        "codex": TeamAdapterSpec(
            adapter_id="codex",
            display_name="Codex",
            supported_roles=roles,
            capabilities=[
                TeamAdapterCapability(name="repo-native-protocol", description="Reads tracked 0t-protocol files and session handoff bundles."),
                TeamAdapterCapability(name="cli-collaboration", description="Consumes 0t team handoff output and submits worker-bridge results back into the session."),
            ],
            metadata={"family": "codex", "preferred_entrypoint": "AGENTS.md"},
        ),
        "claude-code": TeamAdapterSpec(
            adapter_id="claude-code",
            display_name="Claude Code",
            supported_roles=roles,
            capabilities=[
                TeamAdapterCapability(name="repo-native-protocol", description="Reads tracked 0t-protocol files and session handoff bundles."),
                TeamAdapterCapability(name="cli-collaboration", description="Consumes 0t team handoff output and submits worker-bridge results back into the session."),
            ],
            metadata={"family": "claude-code", "preferred_entrypoint": "AGENTS.md"},
        ),
    }
