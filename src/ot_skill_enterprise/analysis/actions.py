from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from ot_skill_enterprise.shared.contracts import ArtifactRef, AnalysisReportDocument

from .models import AnalysisDataNeed, AnalysisPlan, AnalysisRequest, EvidenceBundle, EvidenceFinding
from .workspace import AnalysisWorkspace, iter_workspace_json


def _dump_model(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return value


def _workspace(workspace_dir: Path | str | None = None) -> AnalysisWorkspace:
    return AnalysisWorkspace.from_path(workspace_dir).ensure()


def _normalize_request(request: Optional[AnalysisRequest | Mapping[str, Any]]) -> AnalysisRequest:
    if isinstance(request, AnalysisRequest):
        return request
    payload: dict[str, Any] = {}
    if request is not None:
        payload.update(dict(request))
    return AnalysisRequest.model_validate(payload)


def _slugify(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "analysis"


def _infer_actions(request: AnalysisRequest) -> list[AnalysisDataNeed]:
    topic_text = " ".join(
        [
            request.topic,
            request.objective or "",
            request.scope or "",
            " ".join(request.questions),
            " ".join(request.focus_domains),
        ]
    ).lower()

    actions: list[AnalysisDataNeed] = []

    def add(action: str, reason: str, **inputs: Any) -> None:
        if action not in [item.action for item in actions]:
            actions.append(AnalysisDataNeed(action=action, reason=reason, inputs=inputs))

    if any(keyword in topic_text for keyword in ("wallet", "address", "holder")):
        add("inspect_wallet", "wallet-centric request needs wallet profile data", wallet=request.topic)

    if any(keyword in topic_text for keyword in ("token", "price", "liquidity", "risk", "holder")):
        add("inspect_token", "token-centric request needs token profile data", query=request.topic)

    if any(keyword in topic_text for keyword in ("market", "kline", "trend", "flow", "volume", "chart")):
        add("inspect_market", "market context is needed for the analysis", query=request.topic)

    if any(keyword in topic_text for keyword in ("signal", "hot", "discovery", "watchlist", "opportunity")):
        add("discover_tokens", "discovery step is needed to expand candidates", query=request.topic)
        add("review_signals", "signal feed can validate public activity", chain=request.focus_domains[0] if request.focus_domains else None)

    if not actions:
        add("inspect_token", "default to token profile inspection for general analysis", query=request.topic)

    return actions


def _plan_from_request(request: AnalysisRequest, workspace: AnalysisWorkspace) -> AnalysisPlan:
    data_needs = _infer_actions(request)
    ordered_actions = [item.action for item in data_needs]
    plan_id = request.request_id or f"plan-{_slugify(request.topic)}-{uuid4().hex[:8]}"
    return AnalysisPlan(
        plan_id=plan_id,
        request=request,
        scope=request.scope,
        objective=request.objective or request.topic,
        questions=request.questions,
        data_needs=data_needs,
        ordered_actions=ordered_actions,
        data_artifacts=[],
        metadata={"workspace": str(workspace.root)},
    )


def _artifact_ref(action_name: str, run_id: str, artifact_path: Path) -> dict[str, Any]:
    return ArtifactRef(
        artifact_id=f"{action_name}-{run_id}",
        kind="json",
        uri=str(artifact_path),
        label=f"{action_name} artifact",
        metadata={"subdir": artifact_path.parent.name},
    ).model_dump(mode="json")


def _result_envelope(
    *,
    action: str,
    request_id: str,
    summary: str,
    payload: dict[str, Any],
    artifact_path: Path,
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "operation": action,
        "request_id": request_id,
        "summary": summary,
        "payload": payload,
        "artifacts": [_artifact_ref(action, request_id, artifact_path)],
        "meta": {"provider": "local", "request_id": request_id, "timestamp": datetime.now(timezone.utc).isoformat()},
        "error": None,
    }


def plan_data_needs(
    request: Optional[AnalysisRequest | Mapping[str, Any]] = None,
    *,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    workspace = _workspace(workspace_dir)
    analysis_request = _normalize_request(request)
    plan = _plan_from_request(analysis_request, workspace)
    plan_path = workspace.write_json(workspace.plan_path(), plan.model_dump(mode="json"))
    summary = f"planned {len(plan.data_needs)} data actions for {analysis_request.topic}"
    return _result_envelope(
        action="plan_data_needs",
        request_id=plan.plan_id,
        summary=summary,
        payload={"plan": plan.model_dump(mode="json")},
        artifact_path=plan_path,
    )


def _extract_artifact_summary(path: Path, body: dict[str, Any]) -> tuple[list[EvidenceFinding], list[str], list[str], list[ArtifactRef]]:
    findings: list[EvidenceFinding] = []
    risk_flags: list[str] = []
    unknowns: list[str] = []
    data_sources: list[ArtifactRef] = []

    payload = body
    if isinstance(body.get("response"), dict):
        payload = body["response"]

    operation = str(payload.get("operation") or body.get("operation") or body.get("action") or path.stem).strip()
    request_id = str(payload.get("request_id") or body.get("request_id") or path.stem)
    artifact_ref = ArtifactRef(
        artifact_id=f"{operation}-{request_id}",
        kind="json",
        uri=str(path),
        label=operation,
        metadata={"operation": operation},
    )
    data_sources.append(artifact_ref)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    payload_error = payload.get("error") if isinstance(payload, dict) else None
    if payload_error:
        unknowns.append(f"{operation} returned an upstream error")

    if operation == "inspect_token":
        identity = data.get("identity", {})
        token_name = identity.get("symbol") or identity.get("name") or identity.get("identifier") or "token"
        market = data.get("market_snapshot", {})
        risk = data.get("risk_snapshot", {})
        holder = data.get("holder_snapshot", {})
        price = market.get("price_usd")
        severity = "info"
        if risk.get("risk_level") in {"high", "critical"}:
            severity = "high"
            risk_flags.append(f"{token_name} risk level is {risk.get('risk_level')}")
        if holder.get("top_holder_share_pct") is not None and holder["top_holder_share_pct"] >= 40:
            severity = "medium" if severity == "info" else severity
            risk_flags.append(f"{token_name} top holder concentration is {holder['top_holder_share_pct']}%")
        findings.append(
            EvidenceFinding(
                title=f"{token_name} profile",
                summary=f"{token_name} trades at {price if price is not None else 'unknown'} USD with {risk.get('risk_level', 'unknown')} risk signals.",
                severity=severity,
                evidence_refs=[artifact_ref.artifact_id],
                implication="Token profile should be reviewed before any deeper thesis work.",
                recommendation="Use token and wallet follow-up data to confirm the thesis.",
                metadata={"operation": operation},
            )
        )

    elif operation == "inspect_market":
        pair = data.get("selected_pair", {})
        pair_name = pair.get("identifier") or "market pair"
        swaps = data.get("recent_swaps", [])
        flow = data.get("flow_summary", {})
        severity = "medium" if len(swaps) >= 1 else "info"
        if flow.get("net_flow_usd") is not None and flow.get("net_flow_usd") < 0:
            risk_flags.append(f"{pair_name} has negative net flow")
        findings.append(
            EvidenceFinding(
                title=f"{pair_name} market behavior",
                summary=f"{pair_name} has {len(swaps)} recent swaps and flow summary {json.dumps(flow, ensure_ascii=False)}.",
                severity=severity,
                evidence_refs=[artifact_ref.artifact_id],
                implication="Market structure may confirm or weaken the request thesis.",
                recommendation="Cross-check with token profile and signal feed.",
                metadata={"operation": operation},
            )
        )

    elif operation == "inspect_wallet":
        summary = data.get("wallet_summary", {})
        holdings = data.get("holdings", [])
        activity = data.get("recent_activity", [])
        wallet = summary.get("wallet_address") or "wallet"
        findings.append(
            EvidenceFinding(
                title=f"{wallet} wallet profile",
                summary=f"{wallet} holds {len(holdings)} tokens with {len(activity)} recent activities.",
                severity="info",
                evidence_refs=[artifact_ref.artifact_id],
                implication="Wallet composition helps frame the analysis around smart money or concentration.",
                recommendation="Check whether the wallet aligns with the analysis objective.",
                metadata={"operation": operation},
            )
        )

    elif operation == "discover_tokens":
        tokens = data.get("token_refs", [])
        findings.append(
            EvidenceFinding(
                title="Token discovery",
                summary=f"Discovery returned {len(tokens)} token candidates.",
                severity="info",
                evidence_refs=[artifact_ref.artifact_id],
                implication="Discovery results can seed the next inspection pass.",
                recommendation="Promote high-rank candidates into token inspection.",
                metadata={"operation": operation},
            )
        )

    elif operation == "review_signals":
        signals = data.get("signals", [])
        high_count = sum(1 for item in signals if str(item.get("severity", "")).lower() in {"high", "critical"})
        findings.append(
            EvidenceFinding(
                title="Public signal review",
                summary=f"Signal feed returned {len(signals)} records with {high_count} high-severity items.",
                severity="medium" if high_count else "info",
                evidence_refs=[artifact_ref.artifact_id],
                implication="Signals can be used as a coarse validation layer.",
                recommendation="Compare signal claims against token and market evidence.",
                metadata={"operation": operation},
            )
        )

    else:
        unknowns.append(f"Unsupported data artifact operation: {operation}")

    if not findings:
        unknowns.append(f"No findings generated for {path.name}")

    return findings, risk_flags, unknowns, data_sources


def synthesize_evidence(
    request: Optional[Mapping[str, Any]] = None,
    *,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    workspace = _workspace(workspace_dir)
    plan_path = workspace.plan_path()
    if not plan_path.exists():
        raise FileNotFoundError(f"missing analysis plan artifact: {plan_path}")

    plan = AnalysisPlan.model_validate(workspace.read_json(plan_path))
    request_id = str((request or {}).get("request_id") or f"bundle-{uuid4().hex[:8]}")
    findings: list[EvidenceFinding] = []
    risk_flags: list[str] = []
    unknowns: list[str] = []
    data_sources: list[ArtifactRef] = []

    for path, body in iter_workspace_json(workspace):
        item_findings, item_risks, item_unknowns, item_sources = _extract_artifact_summary(path, body)
        findings.extend(item_findings)
        risk_flags.extend(item_risks)
        unknowns.extend(item_unknowns)
        data_sources.extend(item_sources)

    if not data_sources:
        unknowns.append("No data artifacts available in workspace/data")

    bundle = EvidenceBundle(
        bundle_id=request_id,
        plan_id=plan.plan_id,
        task_summary=plan.objective,
        scope=plan.scope,
        findings=findings,
        risk_flags=sorted(dict.fromkeys(risk_flags)),
        unknowns=sorted(dict.fromkeys(unknowns)),
        data_sources=data_sources,
        artifacts=[],
        metadata={"workspace": str(workspace.root)},
    )

    findings_path = workspace.write_json(workspace.findings_path(), bundle.model_dump(mode="json"))
    bundle.artifacts = [
        ArtifactRef(
            artifact_id=f"synthesize_evidence-{request_id}",
            kind="json",
            uri=str(findings_path),
            label="synthesize_evidence artifact",
            metadata={"subdir": findings_path.parent.name},
        )
    ]
    workspace.write_json(findings_path, bundle.model_dump(mode="json"))
    summary = f"synthesized {len(findings)} findings from {len(data_sources)} data sources"
    return _result_envelope(
        action="synthesize_evidence",
        request_id=request_id,
        summary=summary,
        payload={"bundle": bundle.model_dump(mode="json")},
        artifact_path=findings_path,
    )


def _render_markdown(report: AnalysisReportDocument) -> str:
    lines = [
        f"# Analysis Report",
        "",
        f"## Task Summary",
        report.task_summary,
        "",
        f"## Scope",
        report.scope,
        "",
        f"## Key Findings",
    ]
    if report.key_findings:
        for finding in report.key_findings:
            lines.append(f"- **{finding.title}**: {finding.summary}")
    else:
        lines.append("- No findings were synthesized.")

    lines.extend(["", "## Risk Flags"])
    if report.risk_flags:
        lines.extend(f"- {item}" for item in report.risk_flags)
    else:
        lines.append("- None")

    lines.extend(["", "## Unknowns"])
    if report.unknowns:
        lines.extend(f"- {item}" for item in report.unknowns)
    else:
        lines.append("- None")

    lines.extend(["", "## Data Sources"])
    for source in report.data_sources:
        label = source.label or source.artifact_id
        lines.append(f"- {label}: {source.uri or source.artifact_id}")

    return "\n".join(lines).strip() + "\n"


def write_report(
    request: Optional[Mapping[str, Any]] = None,
    *,
    workspace_dir: Path | str | None = None,
) -> dict[str, Any]:
    workspace = _workspace(workspace_dir)
    findings_path = workspace.findings_path()
    if not findings_path.exists():
        raise FileNotFoundError(f"missing findings artifact: {findings_path}")

    findings_bundle = EvidenceBundle.model_validate(workspace.read_json(findings_path))
    request_id = str((request or {}).get("request_id") or findings_bundle.bundle_id)
    report_md_path = workspace.report_md_path()
    report_json_path = workspace.report_json_path()
    report_artifacts = [
        ArtifactRef(
            artifact_id=f"write_report-{request_id}-md",
            kind="markdown",
            uri=str(report_md_path),
            label="analysis report markdown",
            metadata={"subdir": report_md_path.parent.name},
        ),
        ArtifactRef(
            artifact_id=f"write_report-{request_id}-json",
            kind="json",
            uri=str(report_json_path),
            label="analysis report json",
            metadata={"subdir": report_json_path.parent.name},
        ),
    ]
    report = AnalysisReportDocument(
        task_summary=findings_bundle.task_summary,
        scope=findings_bundle.scope,
        key_findings=findings_bundle.findings,
        risk_flags=findings_bundle.risk_flags,
        unknowns=findings_bundle.unknowns,
        data_sources=findings_bundle.data_sources,
        artifacts=[ref.model_dump(mode="json") for ref in report_artifacts],
        metadata={
            "bundle_id": findings_bundle.bundle_id,
            "plan_id": findings_bundle.plan_id,
            "workspace": str(workspace.root),
        },
    )
    report_json_path = workspace.write_json(report_json_path, report.model_dump(mode="json"))
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(_render_markdown(report), encoding="utf-8")

    summary = f"wrote report with {len(report.key_findings)} findings"
    return {
        "ok": True,
        "action": "write_report",
        "operation": "write_report",
        "request_id": request_id,
        "summary": summary,
        "payload": {
            "report": report.model_dump(mode="json"),
            "report_md_path": str(report_md_path),
            "report_json_path": str(report_json_path),
        },
        "artifacts": [ref.model_dump(mode="json") for ref in report_artifacts],
        "meta": {"provider": "local", "request_id": request_id, "timestamp": datetime.now(timezone.utc).isoformat()},
        "error": None,
    }
