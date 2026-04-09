from __future__ import annotations

from pathlib import Path

from ot_skill_enterprise.workflows import WorkflowRuntime


def _artifact(step_id: str, run_id: str, tmp_path: Path) -> dict[str, object]:
    path = tmp_path / f"{step_id}-{run_id}.json"
    path.write_text("{}", encoding="utf-8")
    return {
        "artifact_id": f"{step_id}-{run_id}",
        "kind": "json",
        "uri": str(path),
        "label": step_id,
        "metadata": {"kind": "unit"},
    }


def test_workflow_runtime_runs_presets_and_aggregates_artifacts(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    run_id = "run-001"

    def handler(step, payload, context):
        calls.append((step.action_id, dict(payload)))
        if step.action_id == "plan_data_needs":
            return {
                "ok": True,
                "summary": "plan created",
                "payload": {"plan": {"plan_id": context.run_id}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        if step.action_id == "inspect_token":
            return {
                "ok": True,
                "summary": "token inspected",
                "payload": {"token_profile": {"identifier": payload["token"]}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        if step.action_id == "inspect_market":
            return {
                "ok": True,
                "summary": "market inspected",
                "payload": {"market_activity": {"token": payload["token"]}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        if step.action_id == "review_signals":
            return {
                "ok": True,
                "summary": "signals reviewed",
                "payload": {"signal_feed": {"token": payload["token"]}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        if step.action_id == "synthesize_evidence":
            return {
                "ok": True,
                "summary": "findings synthesized",
                "payload": {"bundle": {"bundle_id": context.run_id}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        if step.action_id == "write_report":
            return {
                "ok": True,
                "summary": "report written",
                "payload": {"report": {"task_summary": "ok"}},
                "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
                "meta": {"provider": "local"},
                "error": None,
            }
        raise AssertionError(step.action_id)

    runtime = WorkflowRuntime(
        handlers={
            ("analysis-core", "plan_data_needs"): handler,
            ("ave-data-gateway", "inspect_token"): handler,
            ("ave-data-gateway", "inspect_market"): handler,
            ("ave-data-gateway", "review_signals"): handler,
            ("analysis-core", "synthesize_evidence"): handler,
            ("analysis-core", "write_report"): handler,
        }
    )

    result = runtime.run(
        "token_due_diligence",
        {
            "run_id": run_id,
            "topic": "token alpha",
            "objective": "review",
            "target_token_ref": {"identifier": "0xabc", "symbol": "ABC"},
            "chain": "eth",
        },
        run_id=run_id,
        workspace_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert [step["action_id"] for step in result["executed_steps"]] == [
        "plan_data_needs",
        "inspect_token",
        "inspect_market",
        "review_signals",
        "synthesize_evidence",
        "write_report",
    ]
    assert result["artifact_refs"]
    assert len(result["artifact_refs"]) == 6
    assert calls[1][1]["token"] == "0xabc"
    assert calls[2][1]["token"] == "0xabc"
    assert calls[-1][0] == "write_report"


def test_workflow_runtime_stops_on_failure_and_reports_error(tmp_path: Path) -> None:
    run_id = "run-002"

    def ok_handler(step, payload, context):
        return {
            "ok": True,
            "summary": f"{step.action_id} ok",
            "payload": {"payload": dict(payload)},
            "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
            "meta": {"provider": "local"},
            "error": None,
        }

    def failing_handler(step, payload, context):
        return {
            "ok": False,
            "summary": f"{step.action_id} failed",
            "payload": {"payload": dict(payload)},
            "artifacts": [_artifact(step.action_id, context.run_id, tmp_path)],
            "meta": {"provider": "local"},
            "error": {
                "code": "UPSTREAM_HTTP_ERROR",
                "message": "simulated failure",
                "details": {"action_id": step.action_id},
            },
        }

    runtime = WorkflowRuntime(
        handlers={
            ("analysis-core", "plan_data_needs"): ok_handler,
            ("ave-data-gateway", "inspect_wallet"): ok_handler,
            ("ave-data-gateway", "inspect_token"): failing_handler,
        }
    )

    result = runtime.run(
        "wallet_profile",
        {
            "run_id": run_id,
            "topic": "wallet review",
            "wallet_address": "0xwallet",
            "chain": "eth",
        },
        run_id=run_id,
        workspace_dir=tmp_path,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["failure_step_id"] == "inspect_token"
    assert "inspect_token failed" in result["failure_summary"]
    assert [step["action_id"] for step in result["executed_steps"]] == [
        "plan_data_needs",
        "inspect_wallet",
        "inspect_token",
    ]
    assert result["executed_steps"][-1]["status"] == "failed"
    assert result["failure"]["code"] == "UPSTREAM_HTTP_ERROR"
