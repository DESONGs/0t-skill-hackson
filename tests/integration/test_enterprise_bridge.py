from __future__ import annotations

from pathlib import Path

from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge


def test_enterprise_bridge_discovers_local_0t_skills() -> None:
    bridge = EnterpriseBridge.from_project_root()

    local_names = {summary.skill_name for summary in bridge.discover_local_skill_packages()}
    assert {"analysis-core", "ave-data-gateway"}.issubset(local_names)

    vendor_names = {summary.skill_name for summary in bridge.discover_ave_cloud_skill_snapshots()}
    assert {"ave-data-rest", "ave-data-wss", "ave-wallet-suite"}.issubset(vendor_names)

    payload = bridge.runtime_entrypoint()
    assert Path(payload["project_root"]).name == "0t-skill_enterprise"
    discovered_names = {item["skill_name"] for item in payload["local_skills"]}
    assert {"analysis-core", "ave-data-gateway"}.issubset(discovered_names)
    vendor_discovered_names = {item["skill_name"] for item in payload["vendor_ave_cloud_skills"]}
    assert {"ave-data-rest", "ave-data-wss"}.issubset(vendor_discovered_names)

    install = bridge.materialize_local_skill_install("analysis-core")
    assert install.install_root.exists()
    assert (install.install_root / "SKILL.md").exists()
