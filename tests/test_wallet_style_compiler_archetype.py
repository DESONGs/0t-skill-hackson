from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ot_skill_enterprise.skills_compiler.compiler import SkillPackageCompiler
from ot_skill_enterprise.skills_compiler.models import SkillCandidate
from ot_skill_enterprise.skills_compiler.wallet_style_runtime import build_primary_payload
from ot_skill_enterprise.chain_assets import chain_benchmark_defaults


class WalletStyleCompilerArchetypeTests(unittest.TestCase):
    def _candidate(self) -> SkillCandidate:
        defaults = chain_benchmark_defaults("bsc")
        return SkillCandidate(
            candidate_id="candidate-archetype-1",
            candidate_slug="wallet-style-high-frequency-rotator",
            runtime_session_id="session-archetype-1",
            source_run_id="run-001",
            source_evaluation_id="eval-001",
            candidate_type="script",
            target_skill_name="High Frequency Rotator",
            target_skill_kind="wallet_style",
            change_summary="Archetype-aware wallet style package for a high-frequency rotator trader.",
            generation_spec={
                "wallet_style_profile": {
                    "wallet": "0xabc1230000000000000000000000000000000000",
                    "chain": "bsc",
                    "style_label": "high-frequency rotator",
                    "execution_tempo": "same-minute-burst",
                    "risk_appetite": "aggressive-memecoin",
                    "conviction_profile": "layered-profit-recycle",
                    "stablecoin_bias": "minimal",
                    "execution_rules": [
                        "Take quick profits and recycle capital into fresh momentum.",
                    ],
                    "anti_patterns": [
                        "Avoid stale positions when the burst has faded.",
                    ],
                    "archetype": {
                        "primary_archetype": "high_freq_rotator",
                        "secondary_archetypes": ["meme_hunter", "small_cap_chaser"],
                        "behavioral_patterns": [
                            {
                                "pattern_label": "fast_rotation",
                                "strength": 0.93,
                                "evidence": ["trades_per_day=5.20"],
                            },
                            {
                                "pattern_label": "profit_recycling",
                                "strength": 0.86,
                                "evidence": ["profit_add_ratio=0.41"],
                            },
                        ],
                        "archetype_confidence": 0.84,
                        "archetype_evidence_summary": "High trade frequency with repeated profit recycling.",
                        "archetype_token_preference": ["PEPE", "DOGE"],
                    },
                },
                "strategy_spec": {
                    "summary": "Momentum-first strategy built for quick rotation.",
                    "entry_conditions": [],
                    "metadata": {},
                },
                "execution_intent": {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": ["security_token_scan"],
                    "route_preferences": ["WBNB"],
                    "leg_count": 2,
                    "requires_explicit_approval": True,
                    "metadata": {
                        "chain": "bsc",
                        "default_source_token": "USDT",
                        "default_source_token_address": defaults["default_source_token_address"],
                        "default_source_unit_price_usd": defaults["default_source_unit_price_usd"],
                    },
                },
            },
            metadata={
                "skill_family": "wallet_style",
                "wallet_address": "0xabc1230000000000000000000000000000000000",
                "chain": "bsc",
            },
        )

    def _metadata_archetype_candidate(self) -> SkillCandidate:
        candidate = self._candidate()
        profile = candidate.generation_spec["wallet_style_profile"]
        profile["archetype"] = {
            "primary_archetype": "no_stable_archetype",
            "secondary_archetypes": [],
            "behavioral_patterns": [],
            "archetype_confidence": 0.5,
            "archetype_evidence_summary": "",
            "archetype_token_preference": ["DOGE"],
        }
        profile["primary_archetype"] = "no_stable_archetype"
        profile["secondary_archetypes"] = []
        profile["behavioral_patterns"] = []
        profile["archetype_confidence"] = 0.5
        profile["archetype_evidence_summary"] = ""
        profile["archetype_token_preference"] = ["DOGE"]
        profile["metadata"] = {
            **dict(profile.get("metadata") or {}),
            "archetype": {
                "primary_archetype": "scalper",
                "secondary_archetypes": ["meme_hunter"],
                "behavioral_patterns": ["same_minute_burst_scalping"],
                "archetype_confidence": 0.95,
                "archetype_evidence_summary": ["trades_per_day=107.50"],
                "archetype_token_preference": ["UUDOG"],
            },
            "primary_archetype": "scalper",
            "secondary_archetypes": ["meme_hunter"],
            "behavioral_patterns": ["same_minute_burst_scalping"],
            "archetype_confidence": 0.95,
            "archetype_evidence_summary": ["trades_per_day=107.50"],
            "archetype_token_preference": ["UUDOG"],
        }
        return candidate

    def test_compiler_emits_archetype_aware_manifest_and_skill_md(self) -> None:
        candidate = self._candidate()
        compiler = SkillPackageCompiler(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace")

        with TemporaryDirectory() as tmpdir:
            package_root = Path(tmpdir) / "wallet-style-package"
            result = compiler.compile(candidate, output_root=package_root, package_kind="script")

            manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
            skill_md = (package_root / "SKILL.md").read_text(encoding="utf-8")
            archetype_ref = json.loads((package_root / "references" / "archetype.json").read_text(encoding="utf-8"))
            primary_script = (package_root / "scripts" / "primary.py").read_text(encoding="utf-8")

            self.assertEqual(result.manifest["metadata"]["archetype_primary"], "high_freq_rotator")
            self.assertEqual(manifest["metadata"]["trading_archetype"]["primary_archetype"], "high_freq_rotator")
            self.assertIn("This BSC wallet behaves like a high frequency rotator trader", manifest["description"])
            self.assertIn("## Trading Archetype", skill_md)
            self.assertIn("This BSC wallet behaves like a high frequency rotator trader", skill_md)
            self.assertNotIn(" | ", skill_md)
            self.assertIn("Trader class: high frequency rotator.", skill_md)
            self.assertIn("Token preference: PEPE, DOGE", skill_md)
            self.assertEqual(archetype_ref["primary_archetype"], "high_freq_rotator")
            self.assertIn("fast_rotation", archetype_ref["behavioral_pattern_labels"])
            self.assertIn("ARCHETYPE = json.loads", primary_script)
            self.assertIn("archetype=ARCHETYPE", primary_script)

    def test_compiler_prefers_metadata_archetype_over_placeholder_profile_archetype(self) -> None:
        candidate = self._metadata_archetype_candidate()
        compiler = SkillPackageCompiler(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace")

        with TemporaryDirectory() as tmpdir:
            package_root = Path(tmpdir) / "wallet-style-package"
            compiler.compile(candidate, output_root=package_root, package_kind="script")

            manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
            skill_md = (package_root / "SKILL.md").read_text(encoding="utf-8")
            style_profile = manifest["metadata"]["wallet_style_profile"]

            self.assertEqual(manifest["metadata"]["trading_archetype"]["primary_archetype"], "scalper")
            self.assertEqual(style_profile["archetype"]["primary_archetype"], "scalper")
            self.assertEqual(style_profile["metadata"]["archetype"]["primary_archetype"], "scalper")
            self.assertEqual(style_profile["primary_archetype"], "scalper")
            self.assertIn("Trader class: scalper.", skill_md)

    def test_runtime_payload_threads_archetype_into_trade_plan_metadata_and_context_sources(self) -> None:
        candidate = self._candidate()
        style_profile = candidate.generation_spec["wallet_style_profile"]
        strategy = candidate.generation_spec["strategy_spec"]
        execution_intent = candidate.generation_spec["execution_intent"]

        payload = build_primary_payload(
            summary=str(candidate.change_summary),
            profile=style_profile,
            strategy=strategy,
            execution_intent=execution_intent,
            token_catalog={},
            context={
                "chain": "bsc",
                "market_bias": "bullish",
                "target_token": "ALPHA",
                "market_context": {
                    "macro": {"regime": "risk_on"},
                    "focus_token_context": [
                        {
                            "symbol": "ALPHA",
                            "token_address": "0x1111111111111111111111111111111111111111",
                            "price_now": 1.25,
                        }
                    ],
                },
                "candidate_tokens": ["ALPHA"],
            },
            archetype=style_profile["archetype"],
        )

        self.assertEqual(payload["trade_plan"]["trader_archetype"], "high_freq_rotator")
        self.assertEqual(payload["trade_plan"]["archetype"]["primary_archetype"], "high_freq_rotator")
        self.assertIn("high frequency rotator trader", payload["trade_plan"]["trader_archetype_summary"])
        self.assertEqual(payload["metadata"]["primary_archetype"], "high_freq_rotator")
        self.assertIn("fast_rotation", payload["metadata"]["behavioral_pattern_labels"])
        self.assertEqual(payload["metadata"]["archetype"]["primary_archetype"], "high_freq_rotator")
        self.assertEqual(payload["style_profile"]["archetype"]["primary_archetype"], "high_freq_rotator")
        self.assertIn("archetype", payload["context_sources"])
        self.assertEqual(payload["context_sources"]["archetype"]["kind"], "static_payload")
        self.assertIn("high frequency rotator trader", payload["recommendation"]["rationale"][0])

    def test_runtime_payload_prefers_metadata_archetype_over_placeholder_profile_archetype(self) -> None:
        candidate = self._metadata_archetype_candidate()
        style_profile = candidate.generation_spec["wallet_style_profile"]
        strategy = candidate.generation_spec["strategy_spec"]
        execution_intent = candidate.generation_spec["execution_intent"]

        payload = build_primary_payload(
            summary=str(candidate.change_summary),
            profile=style_profile,
            strategy=strategy,
            execution_intent=execution_intent,
            token_catalog={},
            context={
                "chain": "bsc",
                "market_bias": "bullish",
                "target_token": "ALPHA",
                "market_context": {
                    "macro": {"regime": "risk_on"},
                    "focus_token_context": [
                        {
                            "symbol": "ALPHA",
                            "token_address": "0x1111111111111111111111111111111111111111",
                            "price_now": 1.25,
                        }
                    ],
                },
                "candidate_tokens": ["ALPHA"],
            },
            archetype=None,
        )

        self.assertEqual(payload["trade_plan"]["trader_archetype"], "scalper")
        self.assertEqual(payload["trade_plan"]["archetype"]["primary_archetype"], "scalper")
        self.assertEqual(payload["metadata"]["primary_archetype"], "scalper")
        self.assertEqual(payload["style_profile"]["archetype"]["primary_archetype"], "scalper")
        self.assertEqual(payload["style_profile"]["metadata"]["archetype"]["primary_archetype"], "scalper")


if __name__ == "__main__":
    unittest.main()
