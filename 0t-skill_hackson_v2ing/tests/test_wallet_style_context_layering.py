from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ot_skill_enterprise.skills_compiler.wallet_style_runtime import build_primary_payload
from ot_skill_enterprise.style_distillation.context import ReviewAgent, StageCacheRegistry
from ot_skill_enterprise.style_distillation.service import _preprocess_wallet_data


SKILL_ROOT = REPO_ROOT / "skills" / "wallet-style-v2-bsc-d5b63e-5cf11b2a"
EXECUTE_SCRIPT = SKILL_ROOT / "scripts" / "execute.py"
REFERENCES_DIR = SKILL_ROOT / "references"


class WalletStyleContextLayeringTests(unittest.TestCase):
    @staticmethod
    def _load_reference(name: str) -> dict[str, object]:
        return json.loads((REFERENCES_DIR / name).read_text(encoding="utf-8"))

    def _build_payload(self) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
        profile = self._load_reference("style_profile.json")
        strategy = self._load_reference("strategy_spec.json")
        execution_intent = self._load_reference("execution_intent.json")
        token_catalog = self._load_reference("token_catalog.json")
        context = {
            "market_bias": "bullish",
            "candidate_tokens": ["BNBELIEF", "jelly", "ACAI"],
            "preferred_route": ["USDT", "WBNB"],
            "review_hints": {
                "priority": "high",
                "reason": "fresh entry setup",
            },
            "derived_memory": {
                "stage": "distill",
                "resume_from": "reflection",
            },
        }
        payload = build_primary_payload(
            summary=str(profile.get("summary") or ""),
            profile=profile,
            strategy=strategy,
            execution_intent=execution_intent,
            token_catalog=token_catalog,
            context=context,
        )
        return payload, profile, strategy, execution_intent, context

    def test_primary_payload_does_not_alias_inputs(self) -> None:
        payload, profile, strategy, execution_intent, context = self._build_payload()
        original_profile = copy.deepcopy(profile)
        original_strategy = copy.deepcopy(strategy)
        original_execution_intent = copy.deepcopy(execution_intent)
        original_context = copy.deepcopy(context)

        self.assertIsNot(payload["input_context"], context)
        self.assertIsNot(payload["style_profile"], profile)
        self.assertIsNot(payload["strategy"], strategy)
        self.assertIsNot(payload["execution_intent"], execution_intent)

        payload["input_context"]["candidate_tokens"].append("MUTATED")
        payload["style_profile"].setdefault("metadata", {})["mutated"] = True
        payload["strategy"].setdefault("metadata", {})["mutated"] = True
        payload["execution_intent"].setdefault("metadata", {})["mutated"] = True

        self.assertEqual(profile, original_profile)
        self.assertEqual(strategy, original_strategy)
        self.assertEqual(execution_intent, original_execution_intent)
        self.assertEqual(context, original_context)

    def test_primary_payload_reports_context_sources(self) -> None:
        payload, _, _, _, _ = self._build_payload()

        self.assertIn("context_sources", payload)
        context_sources = payload["context_sources"]
        if isinstance(context_sources, dict):
            source_names = set(context_sources)
        else:
            source_names = set(context_sources or [])
        self.assertTrue(
            {
                "style_profile",
                "strategy_spec",
                "execution_intent",
                "token_catalog",
                "input_context",
            }.issubset(source_names)
        )

    def test_saved_snapshot_is_stable_across_resume_retry(self) -> None:
        payload, _, _, _, _ = self._build_payload()
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "primary-stage-snapshot.json"
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            original_snapshot = snapshot_path.read_text(encoding="utf-8")

            first = subprocess.run(
                [sys.executable, str(EXECUTE_SCRIPT), str(snapshot_path)],
                cwd=str(REPO_ROOT),
                check=False,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                [sys.executable, str(EXECUTE_SCRIPT), str(snapshot_path)],
                cwd=str(REPO_ROOT),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)

            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(first_payload["prepared_execution"], second_payload["prepared_execution"])
            self.assertEqual(first_payload["trade_plan"], second_payload["trade_plan"])
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), original_snapshot)

    def test_execute_script_can_resume_from_saved_stage_artifact(self) -> None:
        payload, _, _, _, _ = self._build_payload()
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "resume-stage.json"
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(EXECUTE_SCRIPT), str(snapshot_path)],
                cwd=str(REPO_ROOT),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["trade_plan"]["wallet_address"], payload["trade_plan"]["wallet_address"])
            self.assertEqual(output["trade_plan"]["target_token"], payload["trade_plan"]["target_token"])
            self.assertEqual(output["execution_intent"]["adapter"], payload["execution_intent"]["adapter"])
            self.assertEqual(output["prepared_execution"]["wallet_address"], payload["trade_plan"]["wallet_address"])

    def test_stage_cache_registry_materializes_cross_job_artifacts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            registry = StageCacheRegistry(workspace)
            job_one = workspace / "style-distillations" / "job-one"
            job_two = workspace / "style-distillations" / "job-two"
            job_one.mkdir(parents=True, exist_ok=True)
            job_two.mkdir(parents=True, exist_ok=True)

            payload = {
                "job_id": "job-one",
                "summary": "cached summary",
                "profile": {"summary": "cached"},
            }
            cache_key = "stage-cache-key"
            registry.register(stage="reflection_report", cache_key=cache_key, job_id="job-one", payload=payload, summary="cached summary")

            cached = registry.lookup("reflection_report", cache_key)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["job_id"], "job-one")

            materialized = registry.materialize(job_two, "reflection_report", cache_key)
            self.assertIsNotNone(materialized)
            materialized_payload, materialized_path = materialized  # type: ignore[misc]
            self.assertTrue(materialized_path.is_file())
            self.assertEqual(materialized_payload["cache_hit"], True)
            self.assertEqual(materialized_payload["cache_source_job_id"], "job-one")
            self.assertEqual((job_two / "context" / "stage_reflection.json").is_file(), True)

    def test_review_agent_exposes_contract_hooks(self) -> None:
        agent = ReviewAgent()
        decision = agent.post_stage_call(
            stage="skill_build",
            summary="build complete",
            hints=("keep build canonical",),
            retry_hints=("retry after reflection refresh",),
            context_reduction_hints=("drop stale memory",),
            context_sources=({"kind": "stage_artifact", "identifier": "reflection"},),
        )
        self.assertEqual(decision.stage, "skill_build")
        self.assertIn("keep build canonical", decision.next_stage_hints)
        self.assertIn("retry after reflection refresh", decision.retry_hints)
        self.assertIn("drop stale memory", decision.context_reduction_hints)
        self.assertEqual(decision.context_sources[0]["kind"], "stage_artifact")

    def test_preprocess_consumes_derived_memory(self) -> None:
        wallet_profile = {
            "wallet_summary": {
                "wallet_address": "0xabc",
                "chain": "bsc",
                "balance_usd": 100.0,
                "total_balance_usd": 100.0,
                "total_purchase": 2,
                "total_sold": 1,
            },
            "holdings": [],
            "recent_activity": [],
        }
        compact = _preprocess_wallet_data(
            "0xabc",
            "bsc",
            wallet_profile,
            [],
            {"signals": []},
            derived_memory=[
                {
                    "memory_id": "mem-1",
                    "memory_type": "wallet_style_distillation",
                    "summary": "prefers WBNB rotation",
                    "payload": {
                        "style_label": "burst-rotator",
                        "preferred_tokens": ["WBNB", "BNBELIEF"],
                        "active_windows": ["europe-overlap"],
                    },
                }
            ],
        )
        derived_stats = compact["derived_stats"]
        self.assertIn("WBNB", derived_stats["derived_memory_preferred_tokens"])
        self.assertIn("prefers WBNB rotation", derived_stats["derived_memory_summary"])
        self.assertEqual(derived_stats["derived_memory_count"], 1)
        self.assertIn("burst-rotator", compact["signal_context"]["derived_memory_style_labels"])

    def test_preprocess_surfaces_archetype_fields(self) -> None:
        wallet_profile = {
            "wallet_summary": {
                "wallet_address": "0xdef",
                "chain": "bsc",
                "balance_usd": 250.0,
                "total_balance_usd": 250.0,
                "total_purchase": 4,
                "total_sold": 3,
            },
            "holdings": [],
            "recent_activity": [
                {
                    "action": "buy",
                    "amount_usd": 80.0,
                    "token_ref": {"symbol": "PEPE", "identifier": "bsc:pepe", "token_address": "0xpepe"},
                    "quote_symbol": "USDT",
                }
            ],
        }
        compact = _preprocess_wallet_data(
            "0xdef",
            "bsc",
            wallet_profile,
            [],
            {"signals": []},
            trade_statistics={
                "completed_trade_count": 3,
                "win_rate": 0.67,
                "trades_per_day": 5.2,
                "open_position_ratio": 0.25,
                "pnl_multiplier_max": 2.8,
                "pnl_multiplier_median": 1.3,
                "small_cap_trade_ratio": 0.72,
                "profit_add_ratio": 0.41,
            },
            archetype={
                "trading_archetype": "high_freq_rotator",
                "primary_label": "high_freq_rotator",
                "secondary_archetypes": ["meme_hunter", "degen_sniper"],
                "confidence": 0.81,
                "evidence": ["trades_per_day=5.20", "small_cap_trade_ratio=0.72"],
                "token_preference": ["PEPE", "DOGE"],
                "behavioral_patterns": [
                    {
                        "pattern_label": "fast_rotation",
                        "strength": 0.93,
                        "evidence": ["trades_per_day=5.20"],
                    },
                    {
                        "pattern_label": "small_cap_bias",
                        "strength": 0.88,
                        "evidence": ["small_cap_trade_ratio=0.72"],
                    },
                ],
            },
        )
        self.assertEqual(compact["archetype"]["primary_label"], "high_freq_rotator")
        self.assertEqual(compact["derived_stats"]["primary_archetype"], "high_freq_rotator")
        self.assertIn("meme_hunter", compact["derived_stats"]["secondary_archetypes"])
        self.assertIn("fast_rotation", compact["derived_stats"]["behavioral_patterns"])
        self.assertEqual(compact["behavioral_patterns"][0]["pattern_label"], "fast_rotation")
        self.assertEqual(compact["derived_stats"]["archetype_confidence"], 0.81)
        self.assertIn("PEPE", compact["derived_stats"]["archetype_token_preference"])


if __name__ == "__main__":
    unittest.main()
