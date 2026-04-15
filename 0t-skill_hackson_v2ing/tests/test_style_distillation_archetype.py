from __future__ import annotations

import sys
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ot_skill_enterprise.style_distillation.archetype import ARCHETYPE_TAXONOMY, NO_STABLE_ARCHETYPE, classify_archetype
from ot_skill_enterprise.style_distillation.trade_pairing import compute_trade_statistics, pair_trades


def _activity(
    *,
    action: str,
    timestamp: str,
    tx_hash: str,
    amount_usd: float,
    token_amount: float,
    symbol: str,
    price_usd: float,
) -> dict[str, object]:
    token_address = f"0x{symbol.lower():0>40}"[-42:]
    token_ref = {
        "symbol": symbol,
        "token_address": token_address,
        "identifier": f"bsc:{token_address}",
    }
    payload: dict[str, object] = {
        "tx_hash": tx_hash,
        "timestamp": timestamp,
        "action": action,
        "amount_usd": amount_usd,
        "token_amount": token_amount,
        "token_ref": token_ref,
    }
    if action == "buy":
        payload["to_price_usd"] = price_usd
        payload["from_price_usd"] = price_usd * 1000
    else:
        payload["from_price_usd"] = price_usd
        payload["to_price_usd"] = price_usd / 1000 if price_usd else price_usd
    return payload


class ArchetypeCoreTests(unittest.TestCase):
    def test_trade_pairing_exposes_extended_metadata_and_statistics(self) -> None:
        activities = [
            _activity(action="buy", timestamp="2026-04-13T09:00:00Z", tx_hash="0x1", amount_usd=100.0, token_amount=100.0, symbol="ALPHA", price_usd=1.0),
            _activity(action="sell", timestamp="2026-04-13T10:00:00Z", tx_hash="0x2", amount_usd=150.0, token_amount=100.0, symbol="ALPHA", price_usd=1.5),
            _activity(action="buy", timestamp="2026-04-13T10:30:00Z", tx_hash="0x3", amount_usd=80.0, token_amount=80.0, symbol="ALPHA", price_usd=1.0),
            _activity(action="sell", timestamp="2026-04-13T11:00:00Z", tx_hash="0x4", amount_usd=90.0, token_amount=80.0, symbol="ALPHA", price_usd=1.125),
            _activity(action="buy", timestamp="2026-04-13T11:30:00Z", tx_hash="0x5", amount_usd=60.0, token_amount=60.0, symbol="BETA", price_usd=0.5),
        ]
        completed_trades, open_positions, buy_splits = pair_trades(activities)
        stats = compute_trade_statistics(activities, completed_trades, open_positions, buy_splits)

        self.assertEqual(len(completed_trades), 2)
        self.assertEqual(len(open_positions), 1)
        self.assertTrue(completed_trades[0].is_first_buy_for_token)
        self.assertIn("buy_price_usd", completed_trades[0].metadata)
        self.assertIn("buy_mcap_usd", completed_trades[0].metadata)
        self.assertIn("buy_amount_vs_avg_ratio", completed_trades[1].metadata)
        self.assertTrue(completed_trades[1].metadata["was_in_profit_when_added"])
        self.assertTrue(open_positions[0].metadata["is_first_buy_for_token"])
        self.assertIn("buy_mcap_usd", open_positions[0].metadata)
        self.assertGreater(stats.trades_per_day, 0)
        self.assertGreater(stats.open_position_ratio, 0)
        self.assertGreater(stats.pnl_multiplier_max, 1)
        self.assertGreater(stats.pnl_multiplier_median, 1)
        self.assertGreater(stats.profitable_avg_holding_seconds, 0)
        self.assertGreater(stats.profit_reinvestment_rate, 0)
        self.assertGreater(stats.first_buy_avg_mcap_usd, 0)
        self.assertGreater(stats.small_cap_trade_ratio, 0)
        self.assertGreater(stats.profit_add_ratio, 0)

    def test_classify_archetype_returns_structured_output(self) -> None:
        activities = [
            _activity(action="buy", timestamp="2026-04-13T09:00:00Z", tx_hash="0x1", amount_usd=100.0, token_amount=100.0, symbol="ALPHA", price_usd=1.0),
            _activity(action="sell", timestamp="2026-04-13T10:00:00Z", tx_hash="0x2", amount_usd=150.0, token_amount=100.0, symbol="ALPHA", price_usd=1.5),
            _activity(action="buy", timestamp="2026-04-13T10:30:00Z", tx_hash="0x3", amount_usd=80.0, token_amount=80.0, symbol="ALPHA", price_usd=1.0),
            _activity(action="sell", timestamp="2026-04-13T11:00:00Z", tx_hash="0x4", amount_usd=90.0, token_amount=80.0, symbol="ALPHA", price_usd=1.125),
            _activity(action="buy", timestamp="2026-04-13T11:30:00Z", tx_hash="0x5", amount_usd=60.0, token_amount=60.0, symbol="BETA", price_usd=0.5),
        ]
        completed_trades, open_positions, buy_splits = pair_trades(activities)
        stats = compute_trade_statistics(activities, completed_trades, open_positions, buy_splits)
        archetype = classify_archetype(stats, completed_trades, open_positions)

        self.assertIn(archetype.primary_label, ARCHETYPE_TAXONOMY)
        self.assertNotEqual(archetype.primary_label, NO_STABLE_ARCHETYPE)
        self.assertGreater(archetype.confidence, 0.3)
        self.assertTrue(archetype.behavioral_patterns)
        self.assertTrue(archetype.evidence)
        self.assertTrue(archetype.token_preference)
        self.assertIn("score_map", archetype.metadata)
        self.assertIn("profit_recycling", {pattern.pattern_label for pattern in archetype.behavioral_patterns})

    def test_sparse_input_falls_back_to_no_stable_archetype(self) -> None:
        activities = [
            _activity(action="buy", timestamp="2026-04-13T09:00:00Z", tx_hash="0x1", amount_usd=10.0, token_amount=10.0, symbol="OMEGA", price_usd=1.0),
        ]
        completed_trades, open_positions, buy_splits = pair_trades(activities)
        stats = compute_trade_statistics(activities, completed_trades, open_positions, buy_splits)
        archetype = classify_archetype(stats, completed_trades, open_positions)

        self.assertEqual(archetype.primary_label, NO_STABLE_ARCHETYPE)
        self.assertIn(NO_STABLE_ARCHETYPE, ARCHETYPE_TAXONOMY)
        self.assertGreaterEqual(archetype.confidence, 0.2)


if __name__ == "__main__":
    unittest.main()
