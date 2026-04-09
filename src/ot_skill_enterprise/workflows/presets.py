from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ot_skill_enterprise.shared.contracts.common import ContractModel


ANALYSIS_CORE_SKILL_ID = "analysis-core"
AVE_DATA_GATEWAY_SKILL_ID = "ave-data-gateway"

WORKFLOW_PRESET_NAMES = (
    "token_due_diligence",
    "wallet_profile",
    "hot_market_scan",
)

_ALLOWED_ANALYSIS_ACTIONS = {"plan_data_needs", "synthesize_evidence", "write_report"}
_ALLOWED_GATEWAY_ACTIONS = {
    "discover_tokens",
    "inspect_token",
    "inspect_market",
    "inspect_wallet",
    "review_signals",
}


def normalize_workflow_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


class WorkflowStep(ContractModel):
    step_id: str = Field(min_length=1)
    skill_id: Literal["analysis-core", "ave-data-gateway"]
    action_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    qa_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_step(self) -> "WorkflowStep":
        if self.skill_id == ANALYSIS_CORE_SKILL_ID and self.action_id not in _ALLOWED_ANALYSIS_ACTIONS:
            raise ValueError(f"unsupported analysis-core action: {self.action_id}")
        if self.skill_id == AVE_DATA_GATEWAY_SKILL_ID and self.action_id not in _ALLOWED_GATEWAY_ACTIONS:
            raise ValueError(f"unsupported ave-data-gateway action: {self.action_id}")
        return self


class WorkflowPreset(ContractModel):
    preset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    entry_skill_id: str = ANALYSIS_CORE_SKILL_ID
    support_skill_ids: list[str] = Field(default_factory=lambda: [AVE_DATA_GATEWAY_SKILL_ID])
    graph_type: Literal["dag"] = "dag"
    steps: list[WorkflowStep] = Field(default_factory=list)
    qa_checks: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_preset(self) -> "WorkflowPreset":
        if normalize_workflow_name(self.preset_id) not in WORKFLOW_PRESET_NAMES:
            raise ValueError(f"unknown workflow preset: {self.preset_id}")
        if self.entry_skill_id != ANALYSIS_CORE_SKILL_ID:
            raise ValueError("workflow presets must be anchored by analysis-core")
        if sorted(set(self.support_skill_ids)) != [AVE_DATA_GATEWAY_SKILL_ID]:
            raise ValueError("workflow presets may only depend on ave-data-gateway as a support skill")
        if not self.steps:
            raise ValueError("workflow preset must define at least one step")

        seen_step_ids: set[str] = set()
        skill_ids: set[str] = set()
        for index, step in enumerate(self.steps):
            if step.step_id in seen_step_ids:
                raise ValueError(f"duplicate step_id: {step.step_id}")
            seen_step_ids.add(step.step_id)
            skill_ids.add(step.skill_id)

            missing = [dependency for dependency in step.depends_on if dependency not in seen_step_ids]
            if missing:
                raise ValueError(f"step {step.step_id} depends on unknown step(s): {', '.join(missing)}")

            if index == 0 and step.skill_id != ANALYSIS_CORE_SKILL_ID:
                raise ValueError("workflow presets must start with analysis-core planning")

        if ANALYSIS_CORE_SKILL_ID not in skill_ids:
            raise ValueError("workflow preset must include analysis-core steps")
        if AVE_DATA_GATEWAY_SKILL_ID not in skill_ids:
            raise ValueError("workflow preset must include ave-data-gateway steps")

        first_step = self.steps[0]
        if first_step.action_id != "plan_data_needs":
            raise ValueError("workflow presets must start with plan_data_needs")

        final_step = self.steps[-1]
        if final_step.skill_id != ANALYSIS_CORE_SKILL_ID or final_step.action_id != "write_report":
            raise ValueError("workflow presets must end with analysis-core.write_report")

        return self


_PRESET_CATALOG: dict[str, WorkflowPreset] = {
    "token_due_diligence": WorkflowPreset(
        preset_id="token_due_diligence",
        title="Token Due Diligence",
        summary="Collect token, market, and signal evidence before analysis-core writes a review.",
        steps=[
            WorkflowStep(
                step_id="plan_data_needs",
                skill_id="analysis-core",
                action_id="plan_data_needs",
                purpose="Define the target token, time window, and evidence requirements.",
                outputs=["analysis_plan"],
                qa_notes=["Confirm the target token and scope before any gateway calls."],
            ),
            WorkflowStep(
                step_id="inspect_token",
                skill_id="ave-data-gateway",
                action_id="inspect_token",
                purpose="Fetch token profile, holder concentration, and risk signals.",
                depends_on=["plan_data_needs"],
                inputs=["target_token_ref"],
                outputs=["token_profile"],
            ),
            WorkflowStep(
                step_id="inspect_market",
                skill_id="ave-data-gateway",
                action_id="inspect_market",
                purpose="Fetch price action, liquidity, and swap activity for the token.",
                depends_on=["plan_data_needs", "inspect_token"],
                inputs=["target_token_ref", "analysis_window"],
                outputs=["market_activity"],
            ),
            WorkflowStep(
                step_id="review_signals",
                skill_id="ave-data-gateway",
                action_id="review_signals",
                purpose="Fetch public signal feed and link any relevant token references.",
                depends_on=["plan_data_needs", "inspect_token"],
                inputs=["chain", "target_token_ref"],
                outputs=["signal_feed"],
            ),
            WorkflowStep(
                step_id="synthesize_evidence",
                skill_id="analysis-core",
                action_id="synthesize_evidence",
                purpose="Combine token, market, and signal evidence into findings.",
                depends_on=["inspect_token", "inspect_market", "review_signals"],
                inputs=["token_profile", "market_activity", "signal_feed"],
                outputs=["findings"],
            ),
            WorkflowStep(
                step_id="write_report",
                skill_id="analysis-core",
                action_id="write_report",
                purpose="Produce the final due diligence report and artifact bundle.",
                depends_on=["synthesize_evidence"],
                inputs=["findings"],
                outputs=["report_md", "report_json"],
            ),
        ],
        qa_checks=[
            "Plan step must run before any gateway step.",
            "Final output must be written by analysis-core.write_report.",
            "Report must summarize token, market, and signal evidence.",
        ],
    ),
    "wallet_profile": WorkflowPreset(
        preset_id="wallet_profile",
        title="Wallet Profile",
        summary="Profile a wallet, then inspect its major holdings before analysis-core writes the report.",
        steps=[
            WorkflowStep(
                step_id="plan_data_needs",
                skill_id="analysis-core",
                action_id="plan_data_needs",
                purpose="Define the wallet scope, chain, and analysis depth.",
                outputs=["analysis_plan"],
                qa_notes=["Confirm the wallet address and chain before data retrieval."],
            ),
            WorkflowStep(
                step_id="inspect_wallet",
                skill_id="ave-data-gateway",
                action_id="inspect_wallet",
                purpose="Fetch holdings and recent activity for the wallet.",
                depends_on=["plan_data_needs"],
                inputs=["wallet_address", "chain"],
                outputs=["wallet_profile"],
            ),
            WorkflowStep(
                step_id="inspect_token",
                skill_id="ave-data-gateway",
                action_id="inspect_token",
                purpose="Fetch profiles for the wallet's top holdings.",
                depends_on=["inspect_wallet"],
                inputs=["top_holding_token_refs"],
                outputs=["holding_profiles"],
            ),
            WorkflowStep(
                step_id="inspect_market",
                skill_id="ave-data-gateway",
                action_id="inspect_market",
                purpose="Fetch market activity for the primary holding or most active asset.",
                depends_on=["inspect_wallet", "inspect_token"],
                inputs=["primary_holding_ref"],
                outputs=["market_activity"],
            ),
            WorkflowStep(
                step_id="synthesize_evidence",
                skill_id="analysis-core",
                action_id="synthesize_evidence",
                purpose="Combine wallet, holding, and market evidence into a wallet profile.",
                depends_on=["inspect_wallet", "inspect_token", "inspect_market"],
                inputs=["wallet_profile", "holding_profiles", "market_activity"],
                outputs=["findings"],
            ),
            WorkflowStep(
                step_id="write_report",
                skill_id="analysis-core",
                action_id="write_report",
                purpose="Produce the final wallet profile report and artifact bundle.",
                depends_on=["synthesize_evidence"],
                inputs=["findings"],
                outputs=["report_md", "report_json"],
            ),
        ],
        qa_checks=[
            "Wallet retrieval must complete before token follow-up calls.",
            "Top holding follow-up calls must be derived from inspect_wallet output.",
            "Report must explain wallet balance, holdings, and recent activity.",
        ],
    ),
    "hot_market_scan": WorkflowPreset(
        preset_id="hot_market_scan",
        title="Hot Market Scan",
        summary="Discover active tokens, inspect market behavior, and rank signal quality before reporting.",
        steps=[
            WorkflowStep(
                step_id="plan_data_needs",
                skill_id="analysis-core",
                action_id="plan_data_needs",
                purpose="Define the market scan window, ranking criteria, and signal filters.",
                outputs=["analysis_plan"],
                qa_notes=["Confirm chain and scan window before discovery calls."],
            ),
            WorkflowStep(
                step_id="discover_tokens",
                skill_id="ave-data-gateway",
                action_id="discover_tokens",
                purpose="Discover candidate tokens from the current market surface.",
                depends_on=["plan_data_needs"],
                inputs=["query", "chain", "limit"],
                outputs=["token_candidates"],
            ),
            WorkflowStep(
                step_id="inspect_market",
                skill_id="ave-data-gateway",
                action_id="inspect_market",
                purpose="Inspect price and liquidity behavior for shortlisted tokens.",
                depends_on=["discover_tokens"],
                inputs=["token_candidates"],
                outputs=["market_activity"],
            ),
            WorkflowStep(
                step_id="review_signals",
                skill_id="ave-data-gateway",
                action_id="review_signals",
                purpose="Review signal feed for cross-checks and market event context.",
                depends_on=["discover_tokens"],
                inputs=["chain", "token_candidates"],
                outputs=["signal_feed"],
            ),
            WorkflowStep(
                step_id="synthesize_evidence",
                skill_id="analysis-core",
                action_id="synthesize_evidence",
                purpose="Rank the shortlist using market and signal evidence.",
                depends_on=["inspect_market", "review_signals"],
                inputs=["token_candidates", "market_activity", "signal_feed"],
                outputs=["findings"],
            ),
            WorkflowStep(
                step_id="write_report",
                skill_id="analysis-core",
                action_id="write_report",
                purpose="Produce the final hot market scan report and artifact bundle.",
                depends_on=["synthesize_evidence"],
                inputs=["findings"],
                outputs=["report_md", "report_json"],
            ),
        ],
        qa_checks=[
            "Discovery must precede all market inspection calls.",
            "Report must include the shortlist, market evidence, and signal evidence.",
            "Final report must be written by analysis-core.write_report.",
        ],
    ),
}


def list_workflow_presets() -> list[WorkflowPreset]:
    return [WorkflowPreset.model_validate(_PRESET_CATALOG[name].model_dump(mode="json")) for name in WORKFLOW_PRESET_NAMES]


def get_workflow_preset(name: str) -> WorkflowPreset:
    normalized = normalize_workflow_name(name)
    try:
        return WorkflowPreset.model_validate(_PRESET_CATALOG[normalized].model_dump(mode="json"))
    except KeyError as exc:
        raise KeyError(
            f"unknown workflow preset: {name}. Available presets: {', '.join(WORKFLOW_PRESET_NAMES)}"
        ) from exc


def validate_workflow_preset(preset: WorkflowPreset) -> WorkflowPreset:
    """Return a validated workflow preset instance.

    This is intentionally small so future DAG execution code can reuse the same
    contract validation before turning declarative presets into runnable plans.
    """

    return WorkflowPreset.model_validate(preset.model_dump(mode="json"))
