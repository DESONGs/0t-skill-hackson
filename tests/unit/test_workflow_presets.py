from __future__ import annotations

import pytest
from pydantic import ValidationError

from ot_skill_enterprise.workflows import (
    ANALYSIS_CORE_SKILL_ID,
    AVE_DATA_GATEWAY_SKILL_ID,
    WorkflowPreset,
    WorkflowStep,
    get_workflow_preset,
    list_workflow_presets,
    normalize_workflow_name,
    validate_workflow_preset,
)


def test_catalog_exposes_three_presets_in_expected_order() -> None:
    presets = list_workflow_presets()

    assert [preset.preset_id for preset in presets] == [
        "token_due_diligence",
        "wallet_profile",
        "hot_market_scan",
    ]
    assert all(preset.entry_skill_id == ANALYSIS_CORE_SKILL_ID for preset in presets)
    assert all(AVE_DATA_GATEWAY_SKILL_ID in preset.support_skill_ids for preset in presets)


@pytest.mark.parametrize(
    ("preset_name", "expected_actions"),
    [
        (
            "token_due_diligence",
            [
                "plan_data_needs",
                "inspect_token",
                "inspect_market",
                "review_signals",
                "synthesize_evidence",
                "write_report",
            ],
        ),
        (
            "wallet_profile",
            [
                "plan_data_needs",
                "inspect_wallet",
                "inspect_token",
                "inspect_market",
                "synthesize_evidence",
                "write_report",
            ],
        ),
        (
            "hot_market_scan",
            [
                "plan_data_needs",
                "discover_tokens",
                "inspect_market",
                "review_signals",
                "synthesize_evidence",
                "write_report",
            ],
        ),
    ],
)
def test_presets_keep_the_expected_action_sequence(preset_name: str, expected_actions: list[str]) -> None:
    preset = get_workflow_preset(preset_name)

    assert [step.action_id for step in preset.steps] == expected_actions
    assert preset.steps[0].skill_id == ANALYSIS_CORE_SKILL_ID
    assert preset.steps[-1].skill_id == ANALYSIS_CORE_SKILL_ID
    assert preset.steps[-1].action_id == "write_report"


def test_get_workflow_preset_normalizes_aliases() -> None:
    assert get_workflow_preset("hot-market scan").preset_id == "hot_market_scan"
    assert normalize_workflow_name(" wallet-profile ") == "wallet_profile"


def test_validate_workflow_preset_rejects_bad_graph() -> None:
    bad_preset = WorkflowPreset.model_construct(
        preset_id="token_due_diligence",
        title="Broken",
        summary="Broken preset",
        entry_skill_id=ANALYSIS_CORE_SKILL_ID,
        support_skill_ids=[AVE_DATA_GATEWAY_SKILL_ID],
        graph_type="dag",
        steps=[
            WorkflowStep(
                step_id="plan_data_needs",
                skill_id=ANALYSIS_CORE_SKILL_ID,
                action_id="plan_data_needs",
                purpose="ok",
            ),
            WorkflowStep(
                step_id="write_report",
                skill_id=ANALYSIS_CORE_SKILL_ID,
                action_id="write_report",
                purpose="bad dependency",
                depends_on=["missing_step"],
            ),
        ],
        qa_checks=[],
        metadata={},
    )

    with pytest.raises(ValidationError):
        validate_workflow_preset(bad_preset)


def test_workflow_step_rejects_unknown_action_for_skill() -> None:
    with pytest.raises(ValidationError):
        WorkflowStep(
            step_id="oops",
            skill_id=AVE_DATA_GATEWAY_SKILL_ID,
            action_id="write_report",
            purpose="invalid gateway action",
        )

