from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ot_skill_enterprise.control_plane.cli import main as ot_main
from ot_skill_enterprise.team.protocol import load_team_protocol_bundle
from ot_skill_enterprise.team.service import build_agent_team_service


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SKILL = "meme-hunter-bsc-567a89"


def _build_service(tmp_path: Path):
    return build_agent_team_service(project_root_path=REPO_ROOT, workspace_root=tmp_path / ".ot-workspace")


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVE_DATA_PROVIDER", "mock")
    monkeypatch.setenv("OT_PI_REFLECTION_MOCK", "1")
    monkeypatch.setenv("AVE_USE_DOCKER", "false")
    monkeypatch.setenv("OT_WORKFLOW_RUNTIME", "ts-kernel")
    monkeypatch.delenv("OT_WORKFLOW_ENABLE_PYTHON_FALLBACK", raising=False)


def _write_workspace_config(
    tmp_path: Path,
    *,
    workspace_id: str = "desk-alpha",
    data_source: str = "ave",
    execution: str = "onchainos_cli",
) -> Path:
    path = tmp_path / ".ot-workspace" / "workspaces" / workspace_id / "workflow-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workspace_id": workspace_id,
                "adapter_ids": {
                    "data_source": data_source,
                    "execution": execution,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _create_handoff_ready_session(service, tmp_path: Path) -> tuple[str, dict[str, Any]]:
    completed = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref=SAMPLE_SKILL,
        adapter_family="codex",
    )
    completed_session_id = completed["session"]["session_id"]
    completed_kernel_root = tmp_path / ".ot-workspace" / "runtime-sessions" / completed_session_id / "workflow-kernel"
    seed_response = json.loads(
        completed_kernel_root.joinpath("bridge", "workflow:seed_baseline.response.json").read_text(encoding="utf-8")
    )
    skill_context = service._resolve_skill_context(SAMPLE_SKILL)
    handoff_dispatch = service.kernel_bridge.dispatch(
        workflow_id="autonomous_research",
        request_payload={
            "workflow_id": "autonomous_research",
            "wallet": skill_context["wallet"],
            "chain": skill_context["chain"],
            "skill_name": skill_context["skill_name"],
            "workspace_id": "desk-alpha",
            "workspace_dir": str(tmp_path / ".ot-workspace"),
                "objective": "Optimize wallet-style skill through kernel handoff.",
                "data_source_adapter_id": "ave",
                "execution_adapter_id": "onchainos_cli",
                "metadata": {
                    "source": "0t team",
                    "adapter_family": "codex",
                },
            "operator_hints": {
                "skill_ref": SAMPLE_SKILL,
                "skill_path": skill_context["skill_path"],
                "manifest_path": skill_context["manifest_path"],
            },
        },
        action="handoff",
        metadata={"team_adapter_family": "codex"},
    )
    session_id = handoff_dispatch["kernel_output"]["session"]["session_id"]
    return session_id, seed_response


def test_team_protocol_bundle_normalizes_repo_tracked_specs() -> None:
    bundle = load_team_protocol_bundle(REPO_ROOT)

    workflow = bundle.workflow("autoresearch")
    module = bundle.module("autoresearch")

    assert workflow.module_id == "autoresearch"
    assert workflow.roles == ["planner", "optimizer", "reviewer"]
    assert workflow.team_topology == "homogeneous"
    assert "strategy_spec" in module.search_space_schema["allowed_fields"]
    assert module.capability_type == "workflow_optimizer"


def test_0t_team_doctor_cli_reports_protocol_readiness(tmp_path, capsys) -> None:
    exit_code = ot_main(["team", "--workspace-dir", str(tmp_path / ".ot-workspace"), "doctor"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["workflow_count"] >= 1
    assert {item["adapter_id"] for item in payload["adapters"]} >= {"codex", "claude-code"}
    assert payload["kernel_launch_plan"]["pi_mode"] == "workflow"


def test_0t_team_runs_kernel_owned_autonomous_research_session(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)

    started = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref=SAMPLE_SKILL,
        adapter_family="codex",
    )

    session_id = started["session"]["session_id"]
    kernel_root = tmp_path / ".ot-workspace" / "runtime-sessions" / session_id / "workflow-kernel"

    assert started["session"]["workflow_id"] == "autonomous_research"
    assert started["session"]["active_workflow_id"] == "approval_convergence"
    assert started["session"]["status"] in {"awaiting_approval", "completed", "recommended"}
    assert started["recommendation"] is not None
    assert started["recommendation"]["status"] in {"recommended", "keep", "review_required"}
    assert started["ui_hints"]["handoff_ready_work_items"] == []
    assert kernel_root.joinpath("session.json").is_file()
    assert kernel_root.joinpath("team.json").is_file()
    assert kernel_root.joinpath("result.json").is_file()

    status = service.status(session_id)
    assert status["session"]["session_id"] == session_id
    assert status["session"]["active_workflow_id"] == "approval_convergence"
    assert isinstance(status["work_items"], list)
    assert status["recommendation"] is not None
    assert isinstance(status["leaderboard"], list)
    assert status["approval"] is not None
    assert status["approval"]["status"] in {"pending", "review_required", "approved", "activated", "blocked"}
    assert status["ui_hints"]["handoff_ready_work_items"] == []

    review = service.review(session_id)
    assert review["recommendation"] is not None
    assert review["recommendation"]["status"] in {"recommended", "keep", "review_required"}
    assert review["approval"] is not None
    assert review["ui_hints"]["open_work_items"] == []

    recommendation_variant = review["recommendation"]["variant_id"] or started["recommendation"]["variant_id"]
    approval = service.approve(session_id, variant_id=recommendation_variant, approved_by="human", activate=True)
    assert approval["approval"] is not None
    assert approval["approval"]["status"] == "activated"
    assert approval["activation"]["status"] == "activated"
    assert approval["session"]["status"] == "activated"

    archived = service.archive(session_id)
    assert archived["session"]["status"] == "archived"
    assert archived["approval"] is not None
    assert archived["recommendation"] is not None


def test_0t_team_submit_work_requires_handoff_ready_session(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)
    started = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref=SAMPLE_SKILL,
        adapter_family="codex",
    )

    with pytest.raises(ValueError, match="handoff_ready"):
        service.submit_work(
            started["session"]["session_id"],
            role_id="planner",
            agent_id="codex/planner-1",
            payload={"summary": "manual payload"},
        )


def test_0t_team_submit_work_resumes_kernel_handoff_ready_session(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)
    session_id, seed_response = _create_handoff_ready_session(service, tmp_path)
    handoff = service.handoff(session_id, role_id="optimizer", adapter_family="codex")
    handoff_team_doc = json.loads(
        (tmp_path / ".ot-workspace" / "runtime-sessions" / session_id / "workflow-kernel" / "team.json").read_text(
            encoding="utf-8"
        )
    )
    kernel_agent_session = next(
        item for item in handoff_team_doc["agent_sessions"] if item["work_item_id"] == "workflow:seed_baseline"
    )

    assert handoff["agent_session"]["status"] == "prepared"
    assert handoff["agent_session"]["work_item_id"] == "workflow:seed_baseline"
    assert handoff["agent_session"]["agent_session_id"] == kernel_agent_session["agent_session_id"]
    assert handoff["agent_session"]["metadata"].get("source") != "kernel-fallback"

    submitted = service.submit_work(
        session_id,
        work_item_id=handoff["agent_session"]["work_item_id"],
        agent_id="codex/optimizer-1",
        payload=seed_response,
    )

    assert submitted["session"]["session_id"] == session_id
    assert submitted["session"]["status"] in {"awaiting_approval", "completed", "recommended"}
    assert submitted["recommendation"] is not None

    status = service.status(session_id)
    work_items = {item["work_item_id"]: item for item in status["work_items"]}
    agent_sessions = {item["work_item_id"]: item for item in status["agent_sessions"]}
    assert work_items["workflow:seed_baseline"]["status"] == "completed"
    assert agent_sessions["workflow:seed_baseline"]["status"] == "completed"
    assert agent_sessions["workflow:seed_baseline"]["metadata"]["external_submission"] is True


def test_0t_team_handoff_requires_explicit_supported_adapter_when_kernel_projects_internal_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)
    session_id, _seed_response = _create_handoff_ready_session(service, tmp_path)
    kernel_team_path = tmp_path / ".ot-workspace" / "runtime-sessions" / session_id / "workflow-kernel" / "team.json"
    team_doc = json.loads(kernel_team_path.read_text(encoding="utf-8"))
    team_doc["adapter_family"] = "kernel"
    for item in team_doc.get("agent_sessions") or []:
        item["adapter_family"] = "kernel"
    kernel_team_path.write_text(json.dumps(team_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        type(service.kernel_bridge),
        "dispatch",
        lambda self, **_: {"status": "ran", "kernel_output": {"session": {"session_id": session_id}}},
    )

    with pytest.raises(ValueError, match="pass --adapter explicitly"):
        service.handoff(session_id, role_id="optimizer")


def test_0t_team_submit_work_requires_projected_submit_ready_agent_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)
    session_id, seed_response = _create_handoff_ready_session(service, tmp_path)
    kernel_team_path = tmp_path / ".ot-workspace" / "runtime-sessions" / session_id / "workflow-kernel" / "team.json"
    team_doc = json.loads(kernel_team_path.read_text(encoding="utf-8"))
    team_doc["agent_sessions"] = []
    kernel_team_path.write_text(json.dumps(team_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="no projected agent session"):
        service.submit_work(
            session_id,
            work_item_id="workflow:seed_baseline",
            agent_id="codex/optimizer-1",
            payload=seed_response,
        )


def test_0t_team_persists_under_kernel_runtime_sessions_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)

    started = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref=SAMPLE_SKILL,
        adapter_family="codex",
    )

    session_id = started["session"]["session_id"]
    workspace_root = tmp_path / ".ot-workspace"
    kernel_session_root = workspace_root / "runtime-sessions" / session_id / "workflow-kernel"
    legacy_root = workspace_root / "team" / "sessions" / session_id

    assert kernel_session_root.joinpath("session.json").is_file()
    assert kernel_session_root.joinpath("team.json").is_file()
    assert kernel_session_root.joinpath("result.json").is_file()
    assert not legacy_root.exists()


def test_0t_team_start_requires_explicit_or_workspace_derived_adapters(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    service = _build_service(tmp_path)

    with pytest.raises(ValueError, match="--data-source-adapter"):
        service.start_session(
            "autoresearch",
            workspace_id="desk-alpha",
            skill_ref=SAMPLE_SKILL,
            adapter_family="codex",
        )


def test_0t_team_start_accepts_explicit_adapters(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    service = _build_service(tmp_path)

    started = service.start_session(
        "autoresearch",
        workspace_id="desk-alpha",
        skill_ref=SAMPLE_SKILL,
        adapter_family="codex",
        data_source_adapter_id="ave",
        execution_adapter_id="onchainos_cli",
    )

    request = started["session"]["request"]
    assert request["data_source_adapter_id"] == "ave"
    assert request["execution_adapter_id"] == "onchainos_cli"
    assert request["metadata"]["workspace_adapters"]["data_source"] == "ave"
    assert request["metadata"]["workspace_adapters"]["execution"] == "onchainos_cli"


def test_0t_team_cli_start_reads_workspace_adapter_config(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path, data_source="ave", execution="onchainos_cli")

    exit_code = ot_main(
        [
            "team",
            "--workspace-dir",
            str(tmp_path / ".ot-workspace"),
            "start",
            "autoresearch",
            "--workspace",
            "desk-alpha",
            "--skill",
            SAMPLE_SKILL,
            "--adapter",
            "codex",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    request = payload["session"]["request"]
    assert request["data_source_adapter_id"] == "ave"
    assert request["execution_adapter_id"] == "onchainos_cli"


def test_0t_team_submit_work_requires_formal_worker_contract(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    _write_workspace_config(tmp_path)
    service = _build_service(tmp_path)
    session_id, _seed_response = _create_handoff_ready_session(service, tmp_path)
    handoff = service.handoff(session_id, role_id="optimizer", adapter_family="codex")

    with pytest.raises(ValueError, match="nextgen.worker.response.v1"):
        service.submit_work(
            session_id,
            work_item_id=handoff["agent_session"]["work_item_id"],
            agent_id="codex/optimizer-1",
            payload={"summary": "not a worker contract"},
        )


def test_0t_team_cli_start_passes_explicit_adapter_flags(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    captured: dict[str, Any] = {}

    class _FakeService:
        def start_session(self, workflow_id: str, **kwargs):
            captured["workflow_id"] = workflow_id
            captured["kwargs"] = kwargs
            return {"ok": True}

    monkeypatch.setattr("ot_skill_enterprise.team.service.build_agent_team_service", lambda **_: _FakeService())

    exit_code = ot_main(
        [
            "team",
            "--workspace-dir",
            str(tmp_path / ".ot-workspace"),
            "start",
            "autoresearch",
            "--workspace",
            "desk-alpha",
            "--skill",
            SAMPLE_SKILL,
            "--adapter",
            "codex",
            "--data-source-adapter",
            "fake-data",
            "--execution-adapter",
            "fake-execution",
        ]
    )
    captured_output = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured_output.out) == {"ok": True}
    assert captured["workflow_id"] == "autoresearch"
    assert captured["kwargs"]["data_source_adapter_id"] == "fake-data"
    assert captured["kwargs"]["execution_adapter_id"] == "fake-execution"
