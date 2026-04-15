from __future__ import annotations

import sys
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ot_skill_enterprise.style_distillation.archetype import NO_STABLE_ARCHETYPE
from ot_skill_enterprise.style_distillation.extractors import WalletStyleExtractor


def _activity(timestamp: str, action: str = "buy") -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "action": action,
        "tx_hash": f"0x{timestamp.replace(':', '').replace('-', '').replace('T', '').replace('Z', '')}",
    }


def _build_preprocessed(
    *,
    activity_count: int,
    primary_archetype: str | None,
    archetype_confidence: float | None,
    token_preference: list[str] | None = None,
    secondary_archetypes: list[str] | None = None,
    behavioral_patterns: list[object] | None = None,
    evidence: list[str] | None = None,
    derived_preferred_tokens: list[str] | None = None,
) -> dict[str, object]:
    recent_activity = [_activity(f"2026-04-13T0{i}:00:00Z") for i in range(max(1, activity_count))]
    archetype_payload: dict[str, object] = {
        "primary_archetype": primary_archetype,
        "secondary_archetypes": secondary_archetypes or [],
        "behavioral_patterns": behavioral_patterns or [],
        "archetype_confidence": archetype_confidence,
        "evidence": evidence or [],
        "token_preference": token_preference or [],
    }
    return {
        "wallet": "0xwallet",
        "chain": "bsc",
        "wallet_summary": {"balance_usd": 1_250.0},
        "recent_activity": recent_activity,
        "derived_stats": {
            "activity_count": activity_count,
            "avg_activity_usd": 120.0,
            "top_holding_allocation_pct": 45.0,
            "stablecoin_allocation_pct": 15.0,
            "risky_token_count": 2,
            "dominant_actions": ["buy", "sell"],
            "preferred_tokens": derived_preferred_tokens or ["LEGACY_TOKEN"],
            "archetype": archetype_payload,
            "primary_archetype": primary_archetype,
            "secondary_archetypes": secondary_archetypes or [],
            "behavioral_patterns": behavioral_patterns or [],
            "archetype_confidence": archetype_confidence,
            "archetype_evidence_summary": evidence or [],
            "archetype_token_preference": token_preference or [],
        },
        "archetype": archetype_payload,
    }


class WalletStyleExtractorArchetypeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = WalletStyleExtractor()

    def test_archetype_preferred_over_legacy_heuristic(self) -> None:
        preprocessed = _build_preprocessed(
            activity_count=4,
            primary_archetype="meme_hunter",
            archetype_confidence=0.84,
            token_preference=["NEW_TOKEN", "ALT_TOKEN"],
            secondary_archetypes=["swing_trader"],
            behavioral_patterns=[
                {
                    "pattern_label": "fast_rotation",
                    "strength": 0.92,
                    "evidence": ["trades_per_day=6.00", "avg_holding_hours=2.00"],
                }
            ],
            evidence=["high turnover", "meme liquidity"],
            derived_preferred_tokens=["LEGACY_TOKEN"],
        )

        profile, review = self.extractor.extract(preprocessed)

        self.assertEqual(profile.style_label, "meme_hunter")
        self.assertEqual(profile.preferred_tokens[:2], ("NEW_TOKEN", "ALT_TOKEN"))
        self.assertIn("Honor archetype signal: meme_hunter.", profile.execution_rules)
        self.assertEqual(profile.metadata["archetype"]["primary_archetype"], "meme_hunter")
        self.assertEqual(profile.metadata["archetype"]["token_preference"], ["NEW_TOKEN", "ALT_TOKEN"])
        self.assertEqual(review.status, "generate")
        self.assertTrue(review.should_generate_candidate)
        self.assertEqual(review.metadata["archetype"]["primary_archetype"], "meme_hunter")

    def test_no_stable_archetype_with_single_activity_blocks_generation(self) -> None:
        preprocessed = _build_preprocessed(
            activity_count=1,
            primary_archetype=NO_STABLE_ARCHETYPE,
            archetype_confidence=0.91,
            token_preference=["USDT"],
            evidence=["only one trade"],
        )

        profile, review = self.extractor.extract(preprocessed)

        self.assertEqual(profile.style_label, NO_STABLE_ARCHETYPE)
        self.assertEqual(review.status, "insufficient_signal")
        self.assertFalse(review.should_generate_candidate)

    def test_no_stable_archetype_with_multiple_activities_blocks_generation(self) -> None:
        preprocessed = _build_preprocessed(
            activity_count=3,
            primary_archetype=NO_STABLE_ARCHETYPE,
            archetype_confidence=0.72,
            token_preference=["USDT"],
            evidence=["mixed behavior", "no consistent motif"],
        )

        profile, review = self.extractor.extract(preprocessed)

        self.assertEqual(profile.style_label, NO_STABLE_ARCHETYPE)
        self.assertEqual(review.status, "no_pattern_detected")
        self.assertFalse(review.should_generate_candidate)

    def test_low_confidence_archetype_requests_generation_with_warning(self) -> None:
        preprocessed = _build_preprocessed(
            activity_count=3,
            primary_archetype="swing_trader",
            archetype_confidence=0.42,
            token_preference=["BNB", "WBNB"],
            secondary_archetypes=["compounding_builder"],
            evidence=["slow rotation", "multi-day holds"],
        )

        profile, review = self.extractor.extract(preprocessed)

        self.assertEqual(profile.style_label, "swing_trader")
        self.assertAlmostEqual(profile.confidence, 0.42, places=6)
        self.assertEqual(review.status, "generate_with_low_confidence")
        self.assertTrue(review.should_generate_candidate)


if __name__ == "__main__":
    unittest.main()
