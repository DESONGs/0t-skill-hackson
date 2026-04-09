from __future__ import annotations

import json
from pathlib import Path

from ot_skill_enterprise.analysis import plan_data_needs, synthesize_evidence, write_report


def test_plan_data_needs_writes_deterministic_plan(tmp_path: Path) -> None:
    result = plan_data_needs(
        {
            "topic": "wallet and token analysis for alpha",
            "objective": "build a concise due diligence view",
            "questions": ["Is the wallet concentrated?", "Does token risk look elevated?"],
            "focus_domains": ["eth"],
            "request_id": "req-001",
        },
        workspace_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["action"] == "plan_data_needs"
    assert result["request_id"] == "req-001"

    plan_path = tmp_path / "analysis" / "plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["plan_id"] == "req-001"
    assert plan["request"]["topic"] == "wallet and token analysis for alpha"
    assert [item["action"] for item in plan["data_needs"]] == ["inspect_wallet", "inspect_token"]


def test_synthesize_evidence_reads_workspace_data(tmp_path: Path) -> None:
    plan_result = plan_data_needs({"topic": "token risk review", "objective": "assess downside"}, workspace_dir=tmp_path)
    assert plan_result["ok"] is True

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "inspect_token-abc123.json").write_text(
        json.dumps(
            {
                "action": "inspect_token",
                "request_id": "run-abc123",
                "response": {
                    "ok": True,
                    "operation": "inspect_token",
                    "request_id": "abc123",
                    "data": {
                        "identity": {"identifier": "eth:ave", "symbol": "AVE"},
                        "market_snapshot": {"price_usd": 1.23},
                        "risk_snapshot": {"risk_level": "high"},
                        "holder_snapshot": {"top_holder_share_pct": 55.0},
                    },
                    "meta": {"provider": "ave"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = synthesize_evidence({"request_id": "bundle-001"}, workspace_dir=tmp_path)

    assert result["ok"] is True
    assert result["action"] == "synthesize_evidence"
    findings_path = tmp_path / "analysis" / "findings.json"
    bundle = json.loads(findings_path.read_text(encoding="utf-8"))
    assert bundle["plan_id"] == "req-001" or bundle["plan_id"].startswith("plan-")
    assert bundle["findings"]
    assert any("risk" in item["summary"].lower() or "concentration" in item["summary"].lower() for item in bundle["findings"])
    assert bundle["risk_flags"]


def test_write_report_renders_json_and_markdown(tmp_path: Path) -> None:
    plan_data_needs({"topic": "wallet analysis", "objective": "summarize holdings"}, workspace_dir=tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "inspect_wallet-xyz.json").write_text(
        json.dumps(
            {
                "ok": True,
                "operation": "inspect_wallet",
                "request_id": "xyz",
                "data": {
                    "wallet_summary": {"wallet_address": "0xabc"},
                    "holdings": [{"token_ref": {"identifier": "eth:ave", "symbol": "AVE"}}],
                    "recent_activity": [],
                },
                "meta": {"provider": "ave"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    synthesize_evidence({"request_id": "bundle-002"}, workspace_dir=tmp_path)
    result = write_report({"request_id": "report-002"}, workspace_dir=tmp_path)

    assert result["ok"] is True
    report_json_path = Path(result["payload"]["report_json_path"])
    report_md_path = Path(result["payload"]["report_md_path"])
    assert report_json_path.exists()
    assert report_md_path.exists()

    report = json.loads(report_json_path.read_text(encoding="utf-8"))
    assert report["task_summary"] == "summarize holdings"
    assert report["key_findings"]
    assert report["data_sources"]
    assert "Analysis Report" in report_md_path.read_text(encoding="utf-8")
