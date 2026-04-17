from __future__ import annotations

from ot_skill_enterprise.control_plane.flows.models import FlowTemplate


def build_flow_templates() -> list[FlowTemplate]:
    return [
        FlowTemplate(
            template_id="pi_runtime_session",
            title="Pi Runtime Session",
            description="Primary template for starting an embedded Pi session and recording its runtime artifacts.",
            compatible_agents=("Pi",),
            required_providers=(),
            required_skills=(),
            metadata={"phase": "primary", "mode": "embedded-runtime"},
        ),
        FlowTemplate(
            template_id="external_agent_session",
            title="External Agent Session",
            description="Primary control-plane template for recording and governing runtime sessions emitted by external agents.",
            compatible_agents=("Claude Code", "Codex", "Hermes", "OpenClaw"),
            required_providers=(),
            required_skills=(),
            metadata={"phase": "primary", "mode": "agent-integration"},
        ),
        FlowTemplate(
            template_id="provider_probe",
            title="Provider Probe",
            description="Template for validating provider reachability and recording the resulting diagnostics as runtime artifacts.",
            compatible_agents=("Pi", "Claude Code", "Codex", "Hermes", "OpenClaw"),
            required_providers=("ave",),
            required_skills=("ave-data-gateway",),
            metadata={"phase": "runtime", "mode": "provider-diagnostics"},
        ),
        FlowTemplate(
            template_id="runtime_failure_review",
            title="Runtime Failure Review",
            description="Template for replaying failed runs into QA and evolution without relying on runtime-private fields.",
            compatible_agents=("Pi", "Claude Code", "Codex", "Hermes", "OpenClaw"),
            required_providers=(),
            required_skills=(),
            metadata={"phase": "runtime", "mode": "qa-evolution"},
        ),
        FlowTemplate(
            template_id="wallet_style_distillation",
            title="Wallet Style Distillation",
            description="Hackathon MVP template for distilling a target wallet into a reusable local style skill.",
            compatible_agents=("Pi", "Hermes", "Codex"),
            required_providers=("ave",),
            required_skills=("ave-data-gateway",),
            metadata={"phase": "runtime", "mode": "wallet-style-skill"},
        ),
        FlowTemplate(
            template_id="wallet_style_reflection_review",
            title="Wallet Style Reflection Review",
            description="Pi background reflection template for extracting a structured wallet-style profile before skill generation.",
            compatible_agents=("Pi",),
            required_providers=("ave",),
            required_skills=(),
            metadata={"phase": "runtime", "mode": "wallet-style-reflection"},
        ),
        FlowTemplate(
            template_id="agent_integration_smoke",
            title="Agent Integration Smoke",
            description="Skeleton template for validating agent adapters, traces, and artifact recording.",
            compatible_agents=("Pi", "Claude Code", "Codex", "Hermes", "OpenClaw"),
            required_providers=(),
            required_skills=(),
            metadata={"phase": "skeleton"},
        ),
    ]
