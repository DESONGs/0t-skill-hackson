from __future__ import annotations

import json
from pathlib import Path

from ot_skill_enterprise.control_plane.cli import main as ot_enterprise_main
from ot_skill_enterprise.nextgen import build_next_architecture_service


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_next_architecture_service_overview_reports_builtin_surfaces() -> None:
    service = build_next_architecture_service(project_root=REPO_ROOT)

    overview = service.overview()

    assert overview["status"] == "ready"
    assert overview["target_stack"]["kernel"] == "ts-pi-kernel"
    assert set(overview["plugin_ids"]) >= {"distillation", "autoresearch", "benchmark", "review"}
    assert set(overview["adapter_ids"]) >= {"ave", "onchainos_cli"}
    assert overview["default_adapters"] == {
        "data_source": "ave",
        "execution": "onchainos_cli",
    }


def test_ot_enterprise_architecture_plugins_cli_outputs_registry(capsys) -> None:
    exit_code = ot_enterprise_main(["architecture", "plugins", "--project-root", str(REPO_ROOT)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    plugin_ids = {item["plugin_id"] for item in payload["registry"]["plugins"]}
    workflow_ids = {item["workflow_id"] for item in payload["registry"]["workflows"]}
    assert {"distillation", "autoresearch", "benchmark", "review"} <= plugin_ids
    assert "autonomous_research" in workflow_ids


def test_ot_enterprise_architecture_adapters_cli_outputs_defaults(capsys) -> None:
    exit_code = ot_enterprise_main(["architecture", "adapters", "--project-root", str(REPO_ROOT)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ready"
    assert payload["defaults"] == {
        "data_source": "ave",
        "execution": "onchainos_cli",
    }
    assert payload["capability_matrix"]["wallet_profile"] == ["ave"]
    assert payload["capability_matrix"]["dry_run"] == ["onchainos_cli"]
