from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ot_skill_enterprise.shared.contracts import (
    AnalysisCase,
    AnalysisFeedback,
    AnalysisProposal,
    AnalysisReportBundle,
    AnalysisReportDocument,
    ArtifactRef,
    DiscoverTokensRequest,
    DiscoverTokensResponse,
    EnvelopeMeta,
    InspectTokenRequest,
    ReviewSignalsRequest,
    ServiceEnvelope,
    ServiceError,
    SignalFeedDomain,
    SignalItem,
    TokenDiscoveryDomain,
    TokenReference,
)


def test_ave_data_discovery_envelope_validates_nested_domain() -> None:
    payload = {
        "ok": True,
        "operation": "discover_tokens",
        "request_id": "req-1",
        "data": {
            "token_refs": [
                {
                    "identifier": "0xabc",
                    "chain": "eth",
                    "symbol": "ABC",
                    "rank": 1,
                }
            ],
            "ranking_context": {"title": "hot tokens", "window": "24h"},
            "source_meta": {"provider": "ave", "request_id": "req-1"},
        },
        "meta": {"provider": "ave", "request_id": "req-1"},
    }

    envelope = ServiceEnvelope[TokenDiscoveryDomain].model_validate(payload)

    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.token_refs[0].identifier == "0xabc"
    assert envelope.data.ranking_context is not None
    assert envelope.data.ranking_context.title == "hot tokens"


def test_ave_data_requests_reject_bad_types() -> None:
    with pytest.raises(ValidationError):
        DiscoverTokensRequest(limit=0)

    with pytest.raises(ValidationError):
        InspectTokenRequest(token_ref={"identifier": ""})

    with pytest.raises(ValidationError):
        ReviewSignalsRequest(limit=101)


def test_analysis_report_feedback_case_and_proposal_minimal_shapes() -> None:
    artifact = ArtifactRef(artifact_id="art-1", kind="json", uri="memory://report.json")
    report = AnalysisReportDocument(
        task_summary="Review token quality",
        scope="ABC token",
        key_findings=[{"title": "High concentration", "summary": "Top holders dominate."}],
        risk_flags=["liquidity thin"],
        unknowns=["team ownership not verified"],
        data_sources=[artifact],
        artifacts=[artifact],
    )
    bundle = AnalysisReportBundle(report_md="# report", report_json=report)

    feedback = AnalysisFeedback(
        run_id="run-1",
        skill_id="analysis-core",
        action_id="write_report",
        status="partial",
        summary="Good first pass",
        artifacts=[artifact],
        error_code=None,
    )
    case = AnalysisCase(
        case_id="case-1",
        source={
            "run_id": "run-1",
            "skill_id": "analysis-core",
            "action_id": "write_report",
            "status": "partial",
            "summary": "missing evidence",
        },
        pattern={"problem_type": "incomplete_report", "summary": "report missed a key risk"},
        evidence=[artifact],
        severity="high",
    )
    proposal = AnalysisProposal(
        proposal_id="prop-1",
        case_id="case-1",
        target_skill_name="analysis-core",
        decision_mode="candidate",
        change_summary="Add a risk completeness check",
        target_layer="analysis",
    )

    assert bundle.report_json.task_summary == "Review token quality"
    assert feedback.status == "partial"
    assert case.pattern.problem_type == "incomplete_report"
    assert proposal.target_skill_name == "analysis-core"


def test_signal_feed_domain_uses_structured_items() -> None:
    feed = SignalFeedDomain(
        signals=[
            SignalItem(
                signal_id="sig-1",
                title="volume spike",
                severity="high",
                occurred_at=datetime.now(timezone.utc),
            )
        ],
        linked_token_refs=[TokenReference(identifier="0xabc", symbol="ABC")],
    )

    assert feed.signals[0].title == "volume spike"
    assert feed.linked_token_refs[0].symbol == "ABC"


def test_envelope_rejects_inconsistent_error_state() -> None:
    with pytest.raises(ValidationError):
        ServiceEnvelope[TokenDiscoveryDomain](
            ok=True,
            operation="discover_tokens",
            error=ServiceError(code="boom", message="should not coexist with ok"),
        )
