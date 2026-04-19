from __future__ import annotations

import json
from pathlib import Path

import pytest

from ot_skill_enterprise.control_plane.cli import main as ot_enterprise_main


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_workflow_config(
    tmp_path: Path,
    *,
    workspace_id: str = "desk-alpha",
    data_source: str = "fake-data",
    execution: str = "fake-execution",
) -> Path:
    path = tmp_path / ".ot-workspace" / "workspaces" / workspace_id / "workflow-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "adapter_ids": {
                    "data_source": data_source,
                    "execution": execution,
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _write_kernel_session(
    tmp_path: Path,
    *,
    session_id: str,
    workspace_id: str,
) -> Path:
    path = tmp_path / ".ot-workspace" / "runtime-sessions" / session_id / "workflow-kernel" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "workflow_id": "autonomous_research",
                "request": {
                    "workspace_id": workspace_id,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


class _FakeWorkflowService:
    class _Registry:
        @staticmethod
        def describe() -> dict[str, object]:
            return {
                "plugins": [{"plugin_id": "distillation"}, {"plugin_id": "review"}],
                "workflows": [
                    {"workflow_id": "autonomous_research"},
                    {"workflow_id": "approval_convergence"},
                ],
            }

    plugin_registry = _Registry()

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def run_distillation_seed(self, request):
        self.calls.append(("distillation_seed", request))
        metadata = {
            "kernel_dispatch": {"status": "ran", "workflow_id": request.workflow_id},
            "raw_distillation_result": {
                "promotion": {"promotion_id": "promotion-001"},
                "summary": {"summary": "compat distillation completed"},
            },
        }

        class _Result:
            @staticmethod
            def model_dump(mode="json"):
                return {
                    "workflow_id": request.workflow_id,
                    "data_source_adapter_id": request.data_source_adapter_id,
                    "execution_adapter_id": request.execution_adapter_id,
                    "baseline_variant": {"variant_id": "baseline"},
                    "metadata": metadata,
                }

        _Result.metadata = metadata

        return _Result()

    def run_autonomous_research(self, request):
        self.calls.append(("autonomous_research", request))

        class _Bundle:
            @staticmethod
            def model_dump(mode="json"):
                return {
                    "workflow_id": request.workflow_id,
                    "status": "recommended",
                    "recommended_variant_id": "variant-1",
                    "metadata": {"kernel_dispatch": {"status": "ran", "workflow_id": request.workflow_id}},
                }

        return _Bundle()

    def run_approval_convergence(self, request):
        self.calls.append(("approval_convergence", request))
        approval_granted = bool(request.metadata.get("approval_granted"))
        activation_requested = bool(
            request.metadata.get("activation_requested")
            or request.metadata.get("activate")
        )
        if approval_granted and activation_requested:
            status = "activated"
        elif approval_granted:
            status = "approved"
        else:
            status = "review_required"

        class _Result:
            @staticmethod
            def model_dump(mode="json"):
                return {
                    "workflow_id": request.workflow_id,
                    "session_id": request.session_id,
                    "status": status,
                    "approval": {
                        "status": status,
                        "approval_granted": approval_granted,
                        "activation_requested": activation_requested,
                    },
                    "metadata": {
                        "kernel_dispatch": {"status": "ran", "workflow_id": request.workflow_id},
                        "request_metadata": dict(request.metadata),
                        "request_operator_hints": dict(request.operator_hints),
                        "request_adapters": {
                            "data_source_adapter_id": request.data_source_adapter_id,
                            "execution_adapter_id": request.execution_adapter_id,
                        },
                    },
                }

        return _Result()


class _FakeKernelBridge:
    @staticmethod
    def launch_plan() -> dict[str, object]:
        return {"status": "ready", "kernel_runtime": "pi", "pi_mode": "workflow"}

    @staticmethod
    def dispatch(*, workflow_id: str, request_payload: dict[str, object], allow_failure: bool = True):
        return {
            "status": "ran",
            "workflow_id": workflow_id,
            "request_payload": request_payload,
            "allow_failure": allow_failure,
        }


def test_workflow_overview_cli_reports_kernel_and_registry(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: _FakeWorkflowService())
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(["workflow", "overview", "--project-root", str(REPO_ROOT)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["kernel_launch_plan"]["pi_mode"] == "workflow"
    workflow_ids = {item["workflow_id"] for item in payload["workflow_registry"]["workflows"]}
    assert {"autonomous_research", "approval_convergence"} <= workflow_ids


def test_workflow_autonomous_research_cli_returns_result(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: _FakeWorkflowService())
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    with pytest.raises(SystemExit) as excinfo:
        ot_enterprise_main(
            [
                "workflow",
                "autonomous-research",
                "--project-root",
                str(REPO_ROOT),
                "--wallet",
                "0xabc",
                "--chain",
                "bsc",
            ]
        )

    assert str(excinfo.value) == (
        "workflow execution requires --data-source-adapter or a workspace workflow-config.json with data_source/data_source_adapter_id"
    )


def test_workflow_autonomous_research_cli_reads_workspace_adapter_config(monkeypatch, capsys, tmp_path) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    service = _FakeWorkflowService()
    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())
    _write_workflow_config(tmp_path)

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "autonomous-research",
            "--project-root",
            str(REPO_ROOT),
            "--workspace-dir",
            str(tmp_path / ".ot-workspace"),
            "--workspace",
            "desk-alpha",
            "--wallet",
            "0xabc",
            "--chain",
            "bsc",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    request = service.calls[0][1]
    assert request.workspace_id == "desk-alpha"
    assert request.data_source_adapter_id == "fake-data"
    assert request.execution_adapter_id == "fake-execution"
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["result"]["workflow_id"] == "autonomous_research"
    assert payload["result"]["recommended_variant_id"] == "variant-1"
    assert payload["result"]["metadata"]["kernel_dispatch"]["status"] == "ran"


def test_workflow_wallet_style_distillation_cli_maps_to_distillation_seed_with_explicit_adapter(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    service = _FakeWorkflowService()
    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "wallet-style-distillation",
            "--project-root",
            str(REPO_ROOT),
            "--wallet",
            "0xabc",
            "--chain",
            "bsc",
            "--skill-name",
            "desk-alpha",
            "--data-source-adapter",
            "fake-data",
            "--execution-adapter",
            "fake-execution",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert service.calls[0][0] == "distillation_seed"
    request = service.calls[0][1]
    assert request.workflow_id == "distillation_seed"
    assert request.wallet == "0xabc"
    assert request.data_source_adapter_id == "fake-data"
    assert request.execution_adapter_id == "fake-execution"
    payload = json.loads(captured.out)
    assert payload["result"]["workflow_id"] == "distillation_seed"
    assert payload["result"]["data_source_adapter_id"] == "fake-data"


def test_workflow_wallet_style_distillation_cli_requires_explicit_or_workspace_adapter(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        ot_enterprise_main(
            [
                "workflow",
                "wallet-style-distillation",
                "--project-root",
                str(REPO_ROOT),
                "--wallet",
                "0xabc",
                "--chain",
                "bsc",
            ]
        )

    assert str(excinfo.value) == (
        "workflow execution requires --data-source-adapter or a workspace workflow-config.json with data_source/data_source_adapter_id"
    )


def test_workflow_approval_convergence_cli_passes_explicit_adapters_and_activation_flags(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    service = _FakeWorkflowService()
    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "approval-convergence",
            "--project-root",
            str(REPO_ROOT),
            "--session-id",
            "research-session-1",
            "--approved-by",
            "operator",
            "--approval-granted",
            "--activate",
            "--data-source-adapter",
            "fake-data",
            "--execution-adapter",
            "fake-execution",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert service.calls[0][0] == "approval_convergence"
    request = service.calls[0][1]
    assert request.workflow_id == "approval_convergence"
    assert request.session_id == "research-session-1"
    assert request.data_source_adapter_id == "fake-data"
    assert request.execution_adapter_id == "fake-execution"
    assert request.metadata["approval_granted"] is True
    assert request.metadata["activation_requested"] is True
    assert request.metadata["activate"] is True
    assert request.metadata["approved_by"] == "operator"
    assert request.operator_hints["approval_granted"] is True
    assert request.operator_hints["activation_requested"] is True
    payload = json.loads(captured.out)
    assert payload["result"]["workflow_id"] == "approval_convergence"
    assert payload["result"]["status"] == "activated"


def test_workflow_approval_convergence_cli_defaults_to_no_activation_when_flags_omitted(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    service = _FakeWorkflowService()
    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "approval-convergence",
            "--project-root",
            str(REPO_ROOT),
            "--session-id",
            "research-session-rollback",
            "--data-source-adapter",
            "fake-data",
            "--execution-adapter",
            "fake-execution",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    request = service.calls[0][1]
    assert request.metadata["approval_granted"] is False
    assert request.metadata["activation_requested"] is False
    assert request.metadata["activate"] is False
    assert request.operator_hints["approval_granted"] is False
    assert request.operator_hints["activation_requested"] is False
    payload = json.loads(captured.out)
    assert payload["result"]["status"] == "review_required"
    assert payload["result"]["approval"]["activation_requested"] is False


def test_workflow_approval_convergence_cli_requires_explicit_or_workspace_adapters(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        ot_enterprise_main(
            [
                "workflow",
                "approval-convergence",
                "--project-root",
                str(REPO_ROOT),
                "--session-id",
                "research-session-1",
            ]
        )

    assert str(excinfo.value) == (
        "workflow execution requires --data-source-adapter or a workspace workflow-config.json with data_source/data_source_adapter_id"
    )


def test_workflow_approval_convergence_cli_derives_workspace_from_session(monkeypatch, capsys, tmp_path) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    service = _FakeWorkflowService()
    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())
    _write_workflow_config(tmp_path, workspace_id="desk-alpha", data_source="fake-data", execution="fake-execution")
    _write_kernel_session(tmp_path, session_id="research-session-1", workspace_id="desk-alpha")

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "approval-convergence",
            "--project-root",
            str(REPO_ROOT),
            "--workspace-dir",
            str(tmp_path / ".ot-workspace"),
            "--session-id",
            "research-session-1",
            "--approval-granted",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    request = service.calls[0][1]
    assert request.workspace_id == "desk-alpha"
    assert request.data_source_adapter_id == "fake-data"
    assert request.execution_adapter_id == "fake-execution"
    payload = json.loads(captured.out)
    assert payload["result"]["workflow_id"] == "approval_convergence"


def test_workflow_wallet_style_distillation_cli_uses_formal_operator_command(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: _FakeWorkflowService())
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "wallet-style-distillation",
            "--project-root",
            str(REPO_ROOT),
            "--wallet",
            "0xabc",
            "--chain",
            "bsc",
            "--data-source-adapter",
            "ave",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["result"]["workflow_id"] == "distillation_seed"
    assert payload["result"]["baseline_variant"]["variant_id"] == "baseline"
    assert payload["result"]["metadata"]["kernel_dispatch"]["workflow_id"] == "distillation_seed"


def test_workflow_approval_convergence_cli_returns_result(monkeypatch, capsys) -> None:
    from ot_skill_enterprise import nextgen as nextgen_mod

    monkeypatch.setattr(nextgen_mod, "build_nextgen_workflow_service", lambda **_: _FakeWorkflowService())
    monkeypatch.setattr(nextgen_mod, "build_nextgen_kernel_bridge", lambda **_: _FakeKernelBridge())

    exit_code = ot_enterprise_main(
        [
            "workflow",
            "approval-convergence",
            "--project-root",
            str(REPO_ROOT),
            "--session-id",
            "research-session-approval",
            "--approval-granted",
            "--data-source-adapter",
            "fake-data",
            "--execution-adapter",
            "fake-execution",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["result"]["workflow_id"] == "approval_convergence"
    assert payload["result"]["session_id"] == "research-session-approval"
    assert payload["result"]["approval"]["status"] == "approved"
    assert payload["result"]["metadata"]["kernel_dispatch"]["workflow_id"] == "approval_convergence"


def test_style_distill_compat_shell_uses_ts_kernel_with_compat_adapters(monkeypatch, capsys) -> None:
    import ot_skill_enterprise.nextgen.kernel_bridge as kernel_bridge_mod
    import ot_skill_enterprise.nextgen.workflows as workflow_mod
    import ot_skill_enterprise.style_distillation as style_mod

    service = _FakeWorkflowService()

    class _UnusedLegacyService:
        @staticmethod
        def distill_wallet_style(**_kwargs):
            raise AssertionError("legacy distillation service should not run on the default ts-kernel compat shell")

    monkeypatch.setattr(workflow_mod, "build_nextgen_workflow_service", lambda **_: service)
    monkeypatch.setattr(kernel_bridge_mod, "configured_workflow_runtime", lambda explicit=None: "ts-kernel")
    monkeypatch.setattr(style_mod, "build_wallet_style_distillation_service", lambda **_: _UnusedLegacyService())

    exit_code = ot_enterprise_main(
        [
            "style",
            "distill",
            "--workspace-dir",
            str(REPO_ROOT / ".ot-workspace"),
            "--wallet",
            "0xabc",
            "--chain",
            "bsc",
            "--skill-name",
            "desk-alpha",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert service.calls[0][0] == "distillation_seed"
    request = service.calls[0][1]
    assert request.data_source_adapter_id == "ave"
    assert request.execution_adapter_id == "onchainos_cli"
    payload = json.loads(captured.out)
    assert payload["promotion"]["promotion_id"] == "promotion-001"


def test_nextgen_kernel_bridge_launch_plan_uses_workflow_mode() -> None:
    from ot_skill_enterprise.nextgen import build_nextgen_kernel_bridge

    bridge = build_nextgen_kernel_bridge(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace")
    launch_plan = bridge.launch_plan()

    assert launch_plan["status"] == "ready"
    assert launch_plan["pi_mode"] == "workflow"
    runtime_entry = (REPO_ROOT / "vendor" / "pi_runtime" / "upstream" / "coding_agent" / "src" / "ot_runtime_entry.ts").read_text(encoding="utf-8")
    assert "runWorkflowMode" in runtime_entry
