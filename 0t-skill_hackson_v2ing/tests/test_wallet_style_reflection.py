from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import sys
import unittest
from types import SimpleNamespace

import yaml

from ot_skill_enterprise.control_plane.api import build_control_plane_api
from ot_skill_enterprise.execution import prepare_execution, run_dry_run, run_live
from ot_skill_enterprise.reflection import (
    PiReflectionService,
    ReflectionJobResult,
    ReflectionJobSpec,
    build_wallet_style_output_schema,
    parse_wallet_style_review_report,
)
from ot_skill_enterprise.service_entrypoints import build_ave_provider
from ot_skill_enterprise.style_distillation.backtesting import run_backtest
from ot_skill_enterprise.style_distillation.market_context import TokenMarketContext, summarize_market_payload
from ot_skill_enterprise.style_distillation.signal_filters import build_risk_filters, distill_entry_factors
from ot_skill_enterprise.style_distillation.service import build_wallet_style_distillation_service
from ot_skill_enterprise.style_distillation.service import _pick_focus_tokens
from ot_skill_enterprise.style_distillation.trade_pairing import compute_trade_statistics, pair_trades


REPO_ROOT = Path(__file__).resolve().parents[1]
AVE_SERVICE_ROOT = REPO_ROOT / "services" / "ave-data-service"
if str(AVE_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AVE_SERVICE_ROOT))

from providers import AveRestProvider  # noqa: E402


def _mock_normalized_review(wallet: str, chain: str) -> dict[str, object]:
    target_address = "0x1111111111111111111111111111111111111111"
    source_address = "0x55d398326f99059ff775485246999027b3197955"
    return {
        "profile": {
            "wallet": wallet,
            "chain": chain,
            "style_label": "balanced-position-holding",
            "summary": f"{wallet} on {chain} trades with position holding and balanced risk.",
            "confidence": 0.72,
            "execution_tempo": "position holding",
            "risk_appetite": "balanced",
            "conviction_profile": "single-name conviction",
            "stablecoin_bias": "reserve-heavy",
            "dominant_actions": ["swap", "transfer"],
            "preferred_tokens": ["AVE", "USDT"],
            "active_windows": ["europe-overlap"],
            "sizing_note": "Typical recent ticket is about $2,500.",
            "execution_rules": ["Prefer AVE/USDT rotations.", "Keep reserve-heavy posture."],
            "anti_patterns": ["Do not over-diversify."],
            "prompt_focus": ["risk"],
            "metadata": {"source_activity_count": 2},
        },
        "strategy": {
            "setup_label": "reserve-heavy-rotation",
            "summary": "Rotate from stable quote into AVE when the market is constructive.",
            "entry_conditions": [
                {
                    "condition": "market_bias in ['bullish','range'] and candidate_token == 'AVE'",
                    "data_source": "ave.compact_input.derived_stats",
                    "weight": 0.82,
                    "rationale": "Follow the preferred token rotation.",
                }
            ],
            "exit_conditions": {
                "stop_loss_model": "soft-percent",
                "stop_loss_pct": 12,
                "take_profit_model": "ladder",
            },
            "position_sizing": {
                "model": "split_by_observed_leg_size",
                "max_position_pct": 12,
                "split_legs": True,
                "leg_count": 2,
            },
            "risk_controls": ["block_if_security_scan_fails"],
            "preferred_setups": ["AVE"],
            "invalidation_rules": ["no_chase_after_vertical_move"],
        },
        "execution_intent": {
            "adapter": "onchainos_cli",
            "mode": "dry_run_ready",
            "preferred_workflow": "swap_execute",
            "preflight_checks": ["security_token_scan"],
            "route_preferences": ["USDT", "USDC"],
            "split_legs": True,
            "leg_count": 2,
            "max_position_pct": 12,
            "requires_explicit_approval": True,
            "metadata": {
                "chain": chain,
                "default_target_token": "AVE",
                "default_target_token_address": target_address,
                "default_source_token": "USDT",
                "default_source_token_address": source_address,
            },
        },
        "review": {
            "status": "generate",
            "should_generate_candidate": True,
            "reasoning": "The pattern is consistent enough for an MVP skill.",
            "nudge_prompt": "Generate the wallet style skill now.",
            "metadata": {"prompt_focus": ["risk"]},
        },
    }


class FakeReflectionService:
    def __init__(self, result: ReflectionJobResult) -> None:
        self.result = result
        self.seen_specs: list[ReflectionJobSpec] = []

    def run(self, spec: ReflectionJobSpec) -> ReflectionJobResult:
        self.seen_specs.append(spec)
        return self.result


class FakeVendoredCli:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads

    def run_json(self, command: str, *args: str) -> dict[str, object]:
        payload = self.payloads.get(command)
        if payload is None:
            raise AssertionError(f"unexpected command: {command} {args}")
        return payload


class RecordingRuntimeResult:
    def __init__(self) -> None:
        self.transcript = SimpleNamespace(
            status="succeeded",
            summary="reflection complete",
            output_payload={
                "review_backend": "pi-reflection-mock",
                "raw_output": {"text": "{}"},
                "normalized_output": {},
            },
            metadata={},
            provider_ids=[],
            skill_ids=[],
            ok=True,
        )
        self.pipeline = SimpleNamespace(run=SimpleNamespace(run_id="run-reflection-test"))
        self.session = SimpleNamespace(session_id="pi-session-test")
        self.invocation = SimpleNamespace(invocation_id="pi-invocation-test")

    def as_dict(self, *, full: bool = True) -> dict[str, object]:
        return {
            "runtime_id": "pi",
            "session": {
                "session_id": self.session.session_id,
                "runtime_id": "pi",
                "status": "succeeded",
                "updated_at": "2026-04-14T00:00:00Z",
            },
            "invocation": {
                "invocation_id": self.invocation.invocation_id,
                "status": "succeeded",
                "summary": self.transcript.summary,
                "finished_at": "2026-04-14T00:00:00Z",
            },
            "run_id": self.pipeline.run.run_id,
        }


class RecordingRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RecordingRuntimeResult()


class PagedVendoredCli:
    def run_json(self, command: str, *args: str) -> dict[str, object]:
        if command == "wallet-info":
            return {
                "status": 1,
                "data": {"total_balance": "123.4"},
            }
        if command == "wallet-tokens":
            return {
                "status": 1,
                "data": [
                    {
                        "token": "0x1111111111111111111111111111111111111111",
                        "chain": "bsc",
                        "symbol": "ALPHA",
                        "balance_amount": "1000",
                        "balance_usd": "100",
                    }
                ],
            }
        if command != "address-txs":
            raise AssertionError(f"unexpected command: {command} {args}")
        last_time = None
        for index, value in enumerate(args):
            if value == "--last-time" and index + 1 < len(args):
                last_time = args[index + 1]
                break
        if last_time is None:
            return {
                "status": 1,
                "data": {"result": [{"transaction": "0x1", "time": "2026-04-13T15:00:00Z", "chain": "bsc", "from_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "from_symbol": "WBNB", "from_price_usd": 600, "from_amount": 0.1, "to_address": "0x1111111111111111111111111111111111111111", "to_symbol": "ALPHA", "to_price_usd": 0.000003, "to_amount": 20000000}], "last_time": "2026-04-13T15:00:00Z", "last_id": "0x1"}
            }
        if last_time == "2026-04-13T15:00:00Z":
            return {
                "status": 1,
                "data": {"result": [{"transaction": "0x2", "time": "2026-04-13T16:00:00Z", "chain": "bsc", "from_address": "0x1111111111111111111111111111111111111111", "from_symbol": "ALPHA", "from_price_usd": 0.0000035, "from_amount": 18000000, "to_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "to_symbol": "WBNB", "to_price_usd": 610, "to_amount": 0.11}], "last_time": "2026-04-13T16:00:00Z", "last_id": "0x2"}
            }
        return {"status": 1, "data": {"result": []}}


class TokenEnrichFailProvider:
    def inspect_wallet(self, payload):  # noqa: ANN001
        return {
            "wallet_summary": {
                "wallet_address": payload.wallet,
                "chain": payload.chain,
                "balance_usd": 1024.5,
                "total_balance_usd": 1024.5,
                "token_count": 2,
                "total_profit_ratio": 0.12,
                "total_win_ratio": 0.44,
                "total_purchase": 8,
                "total_sold": 6,
            },
            "holdings": [
                {
                    "token_ref": {
                        "identifier": f"{payload.chain}:0x1111111111111111111111111111111111111111",
                        "chain": payload.chain,
                        "token_address": "0x1111111111111111111111111111111111111111",
                        "symbol": "ALPHA",
                    },
                    "quantity": 100,
                    "value_usd": 512.25,
                    "allocation_pct": 50.0,
                },
                {
                    "token_ref": {
                        "identifier": f"{payload.chain}:0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "chain": payload.chain,
                        "token_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "symbol": "WBNB",
                    },
                    "quantity": 0.5,
                    "value_usd": 512.25,
                    "allocation_pct": 50.0,
                },
            ],
            "recent_activity": [
                {
                    "tx_hash": "0xtest1",
                    "timestamp": "2026-04-13T15:34:12Z",
                    "action": "sell",
                    "token_ref": {
                        "identifier": f"{payload.chain}:0x1111111111111111111111111111111111111111",
                        "chain": payload.chain,
                        "token_address": "0x1111111111111111111111111111111111111111",
                        "symbol": "ALPHA",
                    },
                    "amount_usd": 120.0,
                    "note": "sell:ALPHA->WBNB",
                    "quote_symbol": "WBNB",
                    "from_symbol": "ALPHA",
                    "to_symbol": "WBNB",
                },
                {
                    "tx_hash": "0xtest2",
                    "timestamp": "2026-04-13T15:35:12Z",
                    "action": "buy",
                    "token_ref": {
                        "identifier": f"{payload.chain}:0x2222222222222222222222222222222222222222",
                        "chain": payload.chain,
                        "token_address": "0x2222222222222222222222222222222222222222",
                        "symbol": "BETA",
                    },
                    "amount_usd": 240.0,
                    "note": "buy:WBNB->BETA",
                    "quote_symbol": "WBNB",
                    "from_symbol": "WBNB",
                    "to_symbol": "BETA",
                },
            ],
        }

    def inspect_token(self, payload):  # noqa: ANN001
        raise RuntimeError(f"forced enrich failure for {payload.token_ref.identifier}")

    def review_signals(self, payload):  # noqa: ANN001
        return {
            "signals": [
                {
                    "signal_id": "sig-1",
                    "title": "ALPHA momentum",
                    "severity": "medium",
                    "chain": payload.chain,
                    "token_ref": {
                        "identifier": f"{payload.chain}:0x1111111111111111111111111111111111111111",
                        "chain": payload.chain,
                        "token_address": "0x1111111111111111111111111111111111111111",
                        "symbol": "ALPHA",
                    },
                }
            ]
        }


class AveRestNormalizationTests(unittest.TestCase):
    def test_ave_rest_provider_normalizes_real_wallet_schema(self) -> None:
        provider = AveRestProvider()
        provider._cli = FakeVendoredCli(
            {
                "wallet-info": {
                    "status": 1,
                    "msg": "SUCCESS",
                    "data": {
                        "total_balance": "506226.538627997173",
                        "total_win_ratio": "43.307087",
                        "total_profit_ratio": "0.1198393361837353",
                        "total_purchase": "1357",
                        "total_sold": "1123",
                    },
                },
                "wallet-tokens": {
                    "status": 1,
                    "msg": "SUCCESS",
                    "data": [
                        {
                            "token": "0xb98f1cd9ffde5ad54379e8c9cf7e48e18f8a4444",
                            "chain": "bsc",
                            "symbol": "BOSS",
                            "balance_amount": "0.000000",
                            "balance_usd": "0.000000000000",
                            "total_profit_ratio": "0.1686918653956327",
                        },
                        {
                            "token": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                            "chain": "bsc",
                            "symbol": "WBNB",
                            "balance_amount": "0.053821",
                            "balance_usd": "32.345253622510",
                            "total_profit_ratio": "-0.0008632099454794",
                        },
                    ],
                },
                "address-txs": {
                    "status": 1,
                    "msg": "SUCCESS",
                    "data": {
                        "result": [
                            {
                                "transaction": "0x2d4282f0f92971030f7633f468661e3989e2ceb28153ff57248083f99972d8c9",
                                "time": "2026-04-13T15:34:12.000205Z",
                                "chain": "bsc",
                                "from_address": "0xe549091a2d6072d14f10bd5bb7800175b83a4444",
                                "from_symbol": "草根崛起",
                                "from_price_usd": 4.6338e-06,
                                "from_amount": 21551741.40582,
                                "to_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                "to_symbol": "WBNB",
                                "to_price_usd": 600.89726,
                                "to_amount": 0.1662,
                            },
                            {
                                "transaction": "0x3",
                                "time": "2026-04-13T15:35:12.000205Z",
                                "chain": "bsc",
                                "from_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                "from_symbol": "WBNB",
                                "from_price_usd": 600.89726,
                                "from_amount": 0.12,
                                "to_address": "0x4351c46323bca37e9e2cfc6570c72a2e2f414444",
                                "to_symbol": "拍砖",
                                "to_price_usd": 0.000004,
                                "to_amount": 18000000,
                            },
                        ]
                    },
                },
            }
        )
        wallet = provider.inspect_wallet(
            type(
                "Payload",
                (),
                {"wallet": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1", "chain": "bsc", "include_holdings": True, "include_activity": True},
            )()
        )
        self.assertEqual(wallet["wallet_summary"]["balance_usd"], 506226.53862799716)
        self.assertEqual(wallet["wallet_summary"]["total_profit_ratio"], "0.1198393361837353")
        self.assertEqual(wallet["holdings"][0]["token_ref"]["token_address"], "0xb98f1cd9ffde5ad54379e8c9cf7e48e18f8a4444")
        actions = [item["action"] for item in wallet["recent_activity"]]
        self.assertIn("sell", actions)
        self.assertIn("buy", actions)
        sell_item = next(item for item in wallet["recent_activity"] if item["action"] == "sell")
        buy_item = next(item for item in wallet["recent_activity"] if item["action"] == "buy")
        self.assertEqual(sell_item["token_ref"]["token_address"], "0xe549091a2d6072d14f10bd5bb7800175b83a4444")
        self.assertTrue(buy_item["amount_usd"])

    def test_pick_focus_tokens_filters_placeholder_and_quote_tokens(self) -> None:
        wallet_profile = {
            "recent_activity": [
                {
                    "token_ref": {
                        "identifier": "bsc:token",
                        "chain": "bsc",
                        "token_address": "token",
                        "symbol": None,
                    },
                    "amount_usd": 12,
                },
                {
                    "token_ref": {
                        "identifier": "bsc:0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                        "chain": "bsc",
                        "token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                        "symbol": "WBNB",
                    },
                    "amount_usd": 300,
                },
                {
                    "token_ref": {
                        "identifier": "bsc:0x4351c46323bca37e9e2cfc6570c72a2e2f414444",
                        "chain": "bsc",
                        "token_address": "0x4351c46323bca37e9e2cfc6570c72a2e2f414444",
                        "symbol": "拍砖",
                    },
                    "amount_usd": 280,
                },
            ],
            "holdings": [],
        }
        focus_tokens = _pick_focus_tokens(wallet_profile)
        self.assertEqual(len(focus_tokens), 1)
        self.assertEqual(focus_tokens[0]["token_address"], "0x4351c46323bca37e9e2cfc6570c72a2e2f414444")

    def test_ave_rest_provider_aggregates_activity_pages(self) -> None:
        provider = AveRestProvider()
        provider._cli = PagedVendoredCli()
        wallet = provider.inspect_wallet(
            type(
                "Payload",
                (),
                {
                    "wallet": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "chain": "bsc",
                    "include_holdings": True,
                    "include_activity": True,
                    "activity_pages": 5,
                    "recent_activity_limit": 3,
                },
            )()
        )
        self.assertEqual(len(wallet["full_activity_history"]), 2)
        self.assertEqual(len(wallet["recent_activity"]), 2)
        self.assertEqual(wallet["fetch_metadata"]["activity_pages_fetched"], 3)

    def test_ave_rest_provider_inspect_market_supports_real_pair_shapes(self) -> None:
        provider = AveRestProvider()
        provider._cli = FakeVendoredCli(
            {
                "token": {
                    "status": 1,
                    "data": {
                        "token": {
                            "token_address": "0x1111111111111111111111111111111111111111",
                            "symbol": "ALPHA",
                            "current_price_usd": "0.0000041",
                        },
                        "pairs": [
                            {
                                "pair_address": "0x9999999999999999999999999999999999999999",
                                "chain": "bsc",
                                "target_token": "0x1111111111111111111111111111111111111111",
                                "target_symbol": "ALPHA",
                                "liquidity_usd": "100000",
                                "volume_24h_usd": "250000",
                                "price_change_1h": "11.5",
                                "price_change_24h": "42.0",
                            }
                        ],
                    },
                },
                "pair": {
                    "status": 1,
                    "data": {
                        "pair_address": "0x9999999999999999999999999999999999999999",
                        "token1_address": "0x1111111111111111111111111111111111111111",
                        "token1_symbol": "ALPHA",
                        "token2_address": "0x55d398326f99059ff775485246999027b3197955",
                        "token2_symbol": "USDT",
                        "token1_price_usd": "0.0000045",
                        "tvl": "100000",
                        "volume_u_24h": "250000",
                    },
                },
                "kline-pair": {
                    "status": 1,
                    "data": [
                        {"time": "2026-04-13T10:00:00Z", "close": "0.0000039"},
                        {"time": "2026-04-13T11:00:00Z", "close": "0.0000045"},
                    ],
                },
                "txs": {
                    "status": 1,
                    "data": [
                        {"hash": "0xswap1", "side": "buy"},
                        {"hash": "0xswap2", "side": "sell"},
                    ],
                },
            }
        )
        market = provider.inspect_market(
            type(
                "Payload",
                (),
                {
                    "token_ref": type(
                        "TokenRef",
                        (),
                        {
                            "identifier": "bsc:0x1111111111111111111111111111111111111111",
                            "chain": "bsc",
                            "token_address": "0x1111111111111111111111111111111111111111",
                            "symbol": "ALPHA",
                            "name": "Alpha",
                            "model_dump": lambda self, mode="json": {
                                "identifier": "bsc:0x1111111111111111111111111111111111111111",
                                "chain": "bsc",
                                "token_address": "0x1111111111111111111111111111111111111111",
                                "symbol": "ALPHA",
                                "name": "Alpha",
                            },
                        },
                    )(),
                    "pair_ref": None,
                    "window": "24h",
                    "interval": "60",
                },
            )()
        )
        self.assertEqual(market["selected_pair"]["pair_address"], "0x9999999999999999999999999999999999999999")
        self.assertEqual(len(market["ohlcv"]), 2)
        context = summarize_market_payload(market)
        self.assertEqual(context.symbol, "ALPHA")
        self.assertIsNotNone(context.price_change_1h_pct)
        self.assertIsNotNone(context.volume_to_liquidity_ratio)


class DistillModule兑现Tests(unittest.TestCase):
    def test_trade_pairing_fifo_statistics(self) -> None:
        activities = [
            {
                "tx_hash": "0x1",
                "timestamp": "2026-04-13T10:00:00Z",
                "action": "buy",
                "amount_usd": 100.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
            {
                "tx_hash": "0x2",
                "timestamp": "2026-04-13T10:05:00Z",
                "action": "buy",
                "amount_usd": 120.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
            {
                "tx_hash": "0x3",
                "timestamp": "2026-04-13T11:00:00Z",
                "action": "sell",
                "amount_usd": 140.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
        ]
        completed, open_positions, buy_splits = pair_trades(activities)
        stats = compute_trade_statistics(activities, completed, open_positions, buy_splits)
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(open_positions), 1)
        self.assertEqual(stats.completed_trade_count, 1)
        self.assertEqual(stats.averaging_pattern, "martingale")
        self.assertGreater(stats.win_rate, 0)

    def test_trade_pairing_handles_partial_sell_matching(self) -> None:
        activities = [
            {
                "tx_hash": "0x1",
                "timestamp": "2026-04-13T10:00:00Z",
                "action": "buy",
                "amount_usd": 100.0,
                "token_amount": 10.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
            {
                "tx_hash": "0x2",
                "timestamp": "2026-04-13T10:10:00Z",
                "action": "buy",
                "amount_usd": 200.0,
                "token_amount": 20.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
            {
                "tx_hash": "0x3",
                "timestamp": "2026-04-13T11:00:00Z",
                "action": "sell",
                "amount_usd": 180.0,
                "token_amount": 15.0,
                "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
            },
        ]
        completed, open_positions, _ = pair_trades(activities)
        stats = compute_trade_statistics(activities, completed, open_positions, {})
        self.assertEqual(len(completed), 2)
        self.assertEqual(len(open_positions), 1)
        self.assertGreater(stats.matching_coverage, 0.9)

    def test_entry_factors_and_backtest_are_derived(self) -> None:
        completed, _, _ = pair_trades(
            [
                {
                    "tx_hash": "0x1",
                    "timestamp": "2026-04-13T10:00:00Z",
                    "action": "buy",
                    "amount_usd": 100.0,
                    "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
                },
                {
                    "tx_hash": "0x2",
                    "timestamp": "2026-04-13T11:00:00Z",
                    "action": "sell",
                    "amount_usd": 150.0,
                    "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
                },
            ]
        )
        contexts = [
            TokenMarketContext(
                symbol="ALPHA",
                token_address="0x1111111111111111111111111111111111111111",
                price_now=1.2,
                price_change_1h_pct=-12.0,
                price_change_24h_pct=18.0,
                momentum_label="recovering",
                volatility_regime="high",
                volume_to_liquidity_ratio=2.2,
                liquidity_usd=100000.0,
                volume_24h_usd=220000.0,
            )
        ]
        entry_factors = distill_entry_factors(completed, contexts)
        self.assertTrue(entry_factors)
        backtest = run_backtest(
            {
                "preferred_setups": ["ALPHA"],
                "metadata": {"entry_factors": [item.to_dict() for item in entry_factors]},
            },
            completed,
            contexts,
            signal_context={"active_signals": 1},
        )
        self.assertGreaterEqual(backtest.signal_accuracy, 0)
        self.assertIn(backtest.confidence_label, {"high", "medium", "low", "insufficient_data"})

    def test_backtest_degrades_when_context_and_factors_missing(self) -> None:
        completed, _, _ = pair_trades(
            [
                {
                    "tx_hash": "0x1",
                    "timestamp": "2026-04-13T10:00:00Z",
                    "action": "buy",
                    "amount_usd": 100.0,
                    "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
                },
                {
                    "tx_hash": "0x2",
                    "timestamp": "2026-04-13T10:30:00Z",
                    "action": "sell",
                    "amount_usd": 101.0,
                    "token_ref": {"symbol": "ALPHA", "token_address": "0x1111111111111111111111111111111111111111", "identifier": "bsc:0x1111111111111111111111111111111111111111"},
                },
            ]
        )
        backtest = run_backtest({"preferred_setups": ["ALPHA"], "metadata": {"entry_factors": []}}, completed, [], signal_context={"active_signals": 0})
        self.assertIn(backtest.confidence_label, {"low", "insufficient_data"})
        self.assertTrue(backtest.metadata["baseline_only"])
        self.assertIn("market_context_missing", backtest.metadata["insufficient_reasons"])

    def test_risk_filters_map_holder_and_tax_flags(self) -> None:
        filters = build_risk_filters(
            [
                {
                    "identity": {"symbol": "ALPHA"},
                    "risk_snapshot": {"honeypot": False, "buy_tax_bps": 650, "sell_tax_bps": 300, "flags": ["lp-warning"]},
                    "holder_snapshot": {"top_holder_share_pct": 66.0},
                }
            ]
        )
        filter_types = {item.filter_type for item in filters}
        self.assertIn("high_tax", filter_types)
        self.assertIn("holder_concentration", filter_types)
        self.assertIn("lp_stability", filter_types)


class WalletStyleReflectionTests(unittest.TestCase):
    def test_parse_wallet_style_review_report_success(self) -> None:
        payload = _mock_normalized_review("0xabc", "solana")
        report = parse_wallet_style_review_report(payload, wallet="0xabc", chain="solana")
        self.assertEqual(report.profile.wallet, "0xabc")
        self.assertEqual(report.profile.style_label, "balanced-position-holding")
        self.assertEqual(report.strategy.setup_label, "reserve-heavy-rotation")
        self.assertEqual(report.execution_intent.adapter, "onchainos_cli")
        self.assertTrue(report.review.should_generate_candidate)
        self.assertEqual(report.review.status, "generate")

    def test_parse_wallet_style_review_report_invalid_schema_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_wallet_style_review_report({"profile": {}}, wallet="0xabc", chain="solana")

    def test_reflection_job_embeds_ephemeral_context_outside_system_prompt(self) -> None:
        runtime_service = RecordingRuntimeService()
        service = PiReflectionService(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace", runtime_service=runtime_service)
        spec = ReflectionJobSpec(
            subject_kind="wallet_style_reflection",
            subject_id="0xreflect-context",
            flow_id="wallet_style_reflection_review",
            system_prompt="Return the requested wallet style profile.",
            compact_input={"wallet": "0xreflect-context", "chain": "bsc"},
            expected_output_schema=build_wallet_style_output_schema(),
            artifact_root=REPO_ROOT / ".ot-workspace" / "artifacts",
            prompt="Review the wallet with the supplied context.",
            injected_context={
                "memory": [
                    "Recent review: the wallet prefers small split entries.",
                    "Avoid changing the base prompt wording.",
                ],
                "hints": ["Keep the response strict JSON.", "Treat context as ephemeral."],
                "context_sources": [
                    {
                        "source_id": "session-memory",
                        "source_type": "memory",
                        "summary": "session notes from prior reflection",
                    }
                ],
                "metadata": {"source_mode": "hermes"},
            },
            metadata={"mock_response": _mock_normalized_review("0xreflect-context", "bsc")},
        )

        result = service.run(spec)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(spec.system_prompt, "Return the requested wallet style profile.")
        self.assertEqual(len(runtime_service.calls), 1)

        call = runtime_service.calls[0]
        reflection_job = call["input_payload"]["reflection_job"]
        self.assertEqual(reflection_job["system_prompt"], spec.system_prompt)
        self.assertEqual(reflection_job["user_payload"]["prompt"], spec.prompt)
        self.assertIn("```memory", reflection_job["injected_context"]["fenced_blocks"]["memory"])
        self.assertIn("```hint", reflection_job["injected_context"]["fenced_blocks"]["hints"])
        self.assertEqual(reflection_job["context_sources"][0]["source_id"], "session-memory")
        self.assertEqual(call["input_payload"]["user_payload"]["injected_context"]["metadata"]["source_mode"], "hermes")
        self.assertEqual(call["metadata"]["reflection_context_source_count"], 1)
        self.assertTrue(call["metadata"]["reflection_context_has_content"])
        self.assertEqual(call["metadata"]["reflection_context_sources"][0]["source_type"], "memory")

    def test_reflection_run_does_not_generate_candidate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / ".ot-workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            service = PiReflectionService(project_root=REPO_ROOT, workspace_root=workspace)
            spec = ReflectionJobSpec(
                subject_kind="wallet_style_reflection",
                subject_id="0xreflect1",
                flow_id="wallet_style_reflection_review",
                system_prompt="Return the requested wallet style profile.",
                compact_input={"wallet": "0xreflect1", "chain": "bsc"},
                expected_output_schema=build_wallet_style_output_schema(),
                artifact_root=workspace / "artifacts",
                metadata={"mock_response": _mock_normalized_review("0xreflect1", "bsc")},
            )
            result = service.run(spec)
            self.assertEqual(result.status, "succeeded")
            self.assertFalse(result.fallback_used)
            self.assertTrue(result.reflection_run_id)
            self.assertTrue(result.reflection_session_id)

            api = build_control_plane_api(workspace_dir=workspace)
            self.assertEqual(api.candidate_overview()["candidate_count"], 0)
            self.assertTrue(any((workspace / "evolution-registry" / "runs").glob("*.json")))
            self.assertTrue(any((workspace / "evolution-registry" / "evaluations").glob("*.json")))

    def test_wallet_style_distillation_summary_contains_reflection_lineage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-1",
                    reflection_session_id="pi-session-1",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_normalized_review("0xwallet1001", "bsc"))},
                    normalized_output=_mock_normalized_review("0xwallet1001", "bsc"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet1001", chain="bsc")
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            self.assertEqual(result["review_backend"], "pi-reflection-mock")
            self.assertEqual(result["reflection_flow_id"], "wallet_style_reflection_review")
            self.assertEqual(result["reflection_run_id"], "run-reflection-1")
            self.assertEqual(result["reflection_session_id"], "pi-session-1")
            self.assertEqual(result["reflection_status"], "succeeded")
            self.assertFalse(result["fallback_used"])
            self.assertEqual(result["qa"]["status"], "failed")
            self.assertEqual(result["execution_readiness"], "dry_run_ready")
            self.assertEqual(result["strategy_quality"], "insufficient_data")
            self.assertEqual(result["example_readiness"], "blocked_by_missing_features")
            self.assertTrue(reflection.seen_specs)
            self.assertNotIn("onchainos", json.dumps(reflection.seen_specs[0].compact_input))

            summary_path = Path(result["artifacts"]["job_root"]) / "summary.json"
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["review_backend"], "pi-reflection-mock")
            self.assertEqual(payload["summary"]["reflection_run_id"], "run-reflection-1")
            self.assertFalse(payload["summary"]["fallback_used"])
            self.assertEqual(payload["summary"]["execution_readiness"], "dry_run_ready")
            self.assertEqual(payload["summary"]["strategy_quality"], "insufficient_data")
            self.assertTrue(Path(result["artifacts"]["reflection_result"]).is_file())
            self.assertTrue(Path(result["promotion"]["package_root"]).is_dir())
            self.assertTrue((Path(result["promotion"]["package_root"]) / "scripts" / "execute.py").is_file())
            preprocessed = json.loads(Path(result["artifacts"]["preprocessed_wallet"]).read_text(encoding="utf-8"))
            self.assertIn("recent_trade_samples", preprocessed)
            self.assertIn("top_quote_tokens", preprocessed["derived_stats"])
            self.assertIn("buy_count", preprocessed["derived_stats"])
            self.assertTrue(all("T" not in item for item in preprocessed["derived_stats"]["active_windows"]))
            self.assertIn("market_context", preprocessed)
            self.assertIn("signal_context", preprocessed)
            self.assertLessEqual(preprocessed["compact_size_bytes"], 6144)
            self.assertTrue(result["fetch_metadata"]["parallel"])
            self.assertIn("backtest", result)
            self.assertTrue(Path(result["artifacts"]["backtest_result"]).is_file())

    def test_wallet_style_distillation_fallback_when_reflection_invalid(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-agent:openai/gpt-5.4",
                    reflection_run_id="run-reflection-bad",
                    reflection_session_id="pi-session-bad",
                    status="succeeded",
                    raw_output={"text": "{\"unexpected\":true}"},
                    normalized_output={"unexpected": True},
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet1002", chain="bsc")
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            self.assertTrue(result["fallback_used"])
            self.assertEqual(result["review_backend"], "wallet-style-extractor-fallback")
            self.assertEqual(result["reflection_run_id"], "run-reflection-bad")
            style_review = json.loads(Path(result["artifacts"]["style_review"]).read_text(encoding="utf-8"))
            self.assertTrue(style_review["metadata"]["fallback_used"])
            reflection_result = json.loads(Path(result["artifacts"]["reflection_result"]).read_text(encoding="utf-8"))
            self.assertEqual(reflection_result["status"], "succeeded")

    def test_wallet_style_distillation_continues_when_token_enrich_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-enrich",
                    reflection_session_id="pi-session-enrich",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_normalized_review("0xwallet2001", "bsc"))},
                    normalized_output=_mock_normalized_review("0xwallet2001", "bsc"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=TokenEnrichFailProvider(),
                reflection_service=reflection,
            )
            result = service.distill_wallet_style(wallet="0xwallet2001", chain="bsc")
            self.assertEqual(result["qa"]["status"], "passed")
            self.assertEqual(result["review_backend"], "pi-reflection-mock")
            self.assertEqual(result["execution_readiness"], "dry_run_ready")
            warnings = json.loads(Path(result["artifacts"]["token_enrichment_warnings"]).read_text(encoding="utf-8"))
            self.assertTrue(warnings)
            preprocessed = json.loads(Path(result["artifacts"]["preprocessed_wallet"]).read_text(encoding="utf-8"))
            self.assertEqual(preprocessed["enrichment"]["token_profile_count"], 0)
            self.assertEqual(preprocessed["derived_stats"]["enrich_warning_count"], len(warnings))

    def test_generated_skill_outputs_trade_plan(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-trade-plan",
                    reflection_session_id="pi-session-trade-plan",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_normalized_review("0xwallet3001", "bsc"))},
                    normalized_output=_mock_normalized_review("0xwallet3001", "bsc"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet3001", chain="bsc")
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            script_path = Path(result["promotion"]["package_root"]) / "scripts" / "primary.py"
            bullish_context = {
                "market_bias": "bullish",
                "candidate_tokens": ["AVE"],
                "available_routes": ["USDT"],
                "desired_notional_usd": 900,
                "burst_profile": "short-burst",
                "market_context": {"macro": {"regime": "risk_on"}, "focus_token_context": [{"symbol": "AVE", "price_1h_pct": 12.0, "vol_liq_ratio": 2.1}]},
                "signal_context": {"top_entry_factors": [{"factor_type": "momentum_chase"}], "hard_blocks": [], "warnings": []},
            }
            risk_off_context = {
                **bullish_context,
                "market_bias": "range",
                "market_context": {"macro": {"regime": "risk_off"}, "focus_token_context": []},
                "signal_context": {"top_entry_factors": [], "hard_blocks": ["honeypot_detected"], "warnings": ["lp_stability"]},
            }
            bullish_completed = subprocess.run(
                [sys.executable, str(script_path)],
                input=json.dumps(bullish_context),
                text=True,
                capture_output=True,
                check=False,
            )
            risk_off_completed = subprocess.run(
                [sys.executable, str(script_path)],
                input=json.dumps(risk_off_context),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(bullish_completed.returncode, 0, bullish_completed.stderr)
            self.assertEqual(risk_off_completed.returncode, 0, risk_off_completed.stderr)
            payload = json.loads(bullish_completed.stdout)
            risk_off_payload = json.loads(risk_off_completed.stdout)
            self.assertEqual(payload["recommendation"]["action"], "buy")
            self.assertEqual(risk_off_payload["recommendation"]["action"], "watch")
            self.assertIn("trade_plan", payload)
            self.assertIn("decision_trace", payload)
            self.assertIn("matched_entry_conditions", payload)
            self.assertIn("blocking_reasons", risk_off_payload)
            self.assertNotEqual(payload["trade_plan"]["entry_action"], risk_off_payload["trade_plan"]["entry_action"])
            self.assertEqual(payload["trade_plan"]["mode"], "style-simulated-trade")
            self.assertGreaterEqual(payload["trade_plan"]["leg_count"], 1)
            self.assertEqual(payload["trade_plan"]["target_token"], "AVE")
            self.assertTrue(payload["trade_plan"]["target_token_address"].startswith("0x"))
            execute_path = Path(result["promotion"]["package_root"]) / "scripts" / "execute.py"
            execute_completed = subprocess.run(
                [sys.executable, str(execute_path)],
                input=json.dumps(
                    {
                        "trade_plan": payload["trade_plan"],
                        "execution_intent": payload["execution_intent"],
                        "mode": "prepare_only",
                        "approval_granted": False,
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(execute_completed.returncode, 0, execute_completed.stderr)
            execute_payload = json.loads(execute_completed.stdout)
            self.assertEqual(execute_payload["execution_readiness"], "dry_run_ready")
            self.assertIn("prepared_execution", execute_payload)
            self.assertIn("live_cap_usd", execute_payload)
            self.assertIn("approval_required", execute_payload)

    def test_live_execution_updates_canonical_stage_snapshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-live-update",
                    reflection_session_id="pi-session-live-update",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_normalized_review("0xwallet5001", "bsc"))},
                    normalized_output=_mock_normalized_review("0xwallet5001", "bsc"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                dry_result = service.distill_wallet_style(wallet="0xwallet5001", chain="bsc")
                stage_execution_path = Path(dry_result["artifacts"]["stage_execution"])
                dry_snapshot = json.loads(stage_execution_path.read_text(encoding="utf-8"))
                self.assertIsNone(dry_snapshot["live"])
                self.assertEqual(dry_snapshot["execution_readiness"], "dry_run_ready")

                live_execution_result = {
                    "ok": True,
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "parsed_output": {
                        "summary": "live execution complete",
                        "execution_readiness": "live_ready",
                        "tx_hashes": ["0xlive1"],
                    },
                    "summary": "live execution complete",
                    "execution_readiness": "live_ready",
                    "tx_hashes": ["0xlive1"],
                }
                service._execution_live_test = lambda promoted_root, primary_result, execution_intent: live_execution_result  # type: ignore[assignment]
                live_result = service.resume_job(dry_result["job_id"], live_execute=True, approval_granted=True)
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider

            live_snapshot = json.loads(stage_execution_path.read_text(encoding="utf-8"))
            self.assertEqual(live_snapshot["dry_run"]["execution_readiness"], "dry_run_ready")
            self.assertEqual(live_snapshot["live"]["execution_readiness"], "live_ready")
            self.assertEqual(live_snapshot["live"]["tx_hashes"], ["0xlive1"])
            self.assertEqual(live_snapshot["lineage"]["execution_run_id"], "0xlive1")
            self.assertFalse((stage_execution_path.parent / "execution_live_output.json").exists())

            self.assertEqual(live_result["execution_readiness"], "live_ready")
            self.assertEqual(live_result["example_readiness"], "live_executed")
            self.assertEqual(live_result["lineage"]["execution_run_id"], "0xlive1")
            self.assertEqual(live_result["stage_statuses"]["execution_outcome"]["summary"], "live execution complete")
            self.assertEqual(Path(live_result["artifacts"]["stage_execution"]).resolve(), stage_execution_path.resolve())
            self.assertEqual(Path(live_result["artifacts"]["stage_execution"]).read_text(encoding="utf-8"), stage_execution_path.read_text(encoding="utf-8"))

    def test_prepare_execution_maps_trade_plan_to_onchainos_contract(self) -> None:
        prepared = prepare_execution(
            {
                "chain": "bsc",
                "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                "target_token": "AVE",
                "target_token_address": "0x1111111111111111111111111111111111111111",
                "execution_source_symbol": "USDT",
                "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
                "per_leg_usd": 300,
                "leg_count": 2,
            },
            {
                "adapter": "onchainos_cli",
                "mode": "dry_run_ready",
                "preferred_workflow": "swap_execute",
                "preflight_checks": ["security_token_scan"],
                "route_preferences": ["USDT", "USDC"],
                "leg_count": 2,
                "requires_explicit_approval": True,
            },
            project_root=REPO_ROOT,
        )
        self.assertEqual(prepared["adapter"], "onchainos_cli")
        self.assertIn("wallet_login", prepared["command_groups"])
        self.assertIn("swap_execute", prepared["command_groups"])
        self.assertIn("security_token_scan", prepared["command_groups"])
        self.assertIn("swap_swap", prepared["command_groups"])
        self.assertEqual(prepared["live_cap_usd"], 10.0)
        self.assertEqual(prepared["leg_count"], 2)
        self.assertEqual(prepared["per_leg_usd"], 5.0)

    def test_run_dry_run_blocked_without_okx_credentials(self) -> None:
        previous = {key: os.environ.get(key) for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")}
        try:
            for key in previous:
                os.environ.pop(key, None)
            result = run_dry_run(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "AVE",
                    "target_token_address": "0x1111111111111111111111111111111111111111",
                    "execution_source_symbol": "USDT",
                    "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
                    "per_leg_usd": 300,
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": ["security_token_scan"],
                    "requires_explicit_approval": True,
                },
                project_root=REPO_ROOT,
            )
        finally:
            for key, value in previous.items():
                if value is not None:
                    os.environ[key] = value
        self.assertEqual(result["execution_readiness"], "blocked_by_config")

    def test_run_dry_run_blocked_without_onchainos_cli(self) -> None:
        previous = {key: os.environ.get(key) for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "OT_ONCHAINOS_CLI_BIN")}
        try:
            os.environ["OKX_API_KEY"] = "okx-ak"
            os.environ["OKX_SECRET_KEY"] = "okx-sk"
            os.environ["OKX_PASSPHRASE"] = "okx-pp"
            os.environ["OT_ONCHAINOS_CLI_BIN"] = "/tmp/definitely-missing-onchainos-cli"
            result = run_dry_run(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "AVE",
                    "target_token_address": "0x1111111111111111111111111111111111111111",
                    "execution_source_symbol": "USDT",
                    "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
                    "per_leg_usd": 300,
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": ["security_token_scan"],
                    "requires_explicit_approval": True,
                },
                project_root=REPO_ROOT,
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(result["execution_readiness"], "blocked_by_config")
        self.assertFalse(result["ok"])

    def test_run_dry_run_treats_approval_prereq_as_ready(self) -> None:
        previous = {key: os.environ.get(key) for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "OT_ONCHAINOS_CLI_BIN")}
        responses = iter(
            [
                subprocess.CompletedProcess(args=["wallet", "login"], returncode=0, stdout='{"ok": true, "data": {"accountId": "acct"}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "status"], returncode=0, stdout='{"ok": true, "data": {"loggedIn": true}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "addresses"], returncode=0, stdout='{"ok": true, "data": {"evm": [{"address": "0x2222222222222222222222222222222222222222", "chainIndex": "56", "chainName": "bnb"}]}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "balance"], returncode=0, stdout='{"ok": true, "data": {"totalValueUsd": "25.00"}}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "swap"], returncode=0, stdout='{"ok": true, "data": [{"routerResult": {"fromTokenAmount": "300000000"}, "tx": {"to": "0x3156020dfF8D99af1dDC523ebDfb1ad2018554a0", "data": "0xabcdef", "value": "0"}}]}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "check-approvals"], returncode=0, stdout='{"ok": true, "data": [{"tokens": [{"spendable": "0"}]}]}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "approve"], returncode=0, stdout='{"ok": true, "data": [{"data": "0xapprove"}]}', stderr=""),
                subprocess.CompletedProcess(args=["gateway", "simulate"], returncode=0, stdout='{"ok": true, "data": [{"failReason": "", "risks": []}]}', stderr=""),
            ]
        )

        def _executor(*args, **kwargs):
            return next(responses)

        try:
            os.environ["OKX_API_KEY"] = "okx-ak"
            os.environ["OKX_SECRET_KEY"] = "okx-sk"
            os.environ["OKX_PASSPHRASE"] = "okx-pp"
            os.environ["OT_ONCHAINOS_CLI_BIN"] = "/bin/echo"
            result = run_dry_run(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "AVE",
                    "target_token_address": "0x1111111111111111111111111111111111111111",
                    "execution_source_symbol": "USDT",
                    "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
                    "execution_source_readable_amount": 300,
                    "per_leg_usd": 300,
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": [],
                    "requires_explicit_approval": True,
                },
                project_root=REPO_ROOT,
                executor=_executor,
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(result["execution_readiness"], "dry_run_ready")
        self.assertTrue(result["ok"])
        self.assertTrue(result["approval_required"])
        self.assertIn("approve", result["approval_result"])
        self.assertEqual(result["simulation_result"].get("kind"), "approval")
        self.assertTrue(result["metadata"].get("swap_simulation_skipped"))

    def test_run_live_reuses_preflight_and_caps_notional(self) -> None:
        previous = {key: os.environ.get(key) for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE", "OT_ONCHAINOS_CLI_BIN")}
        calls: list[list[str]] = []
        responses = iter(
            [
                subprocess.CompletedProcess(args=["wallet", "login"], returncode=0, stdout='{"ok": true, "data": {"accountId": "acct"}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "status"], returncode=0, stdout='{"ok": true, "data": {"loggedIn": true}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "addresses"], returncode=0, stdout='{"ok": true, "data": {"evm": [{"address": "0x2222222222222222222222222222222222222222", "chainIndex": "56", "chainName": "bnb"}]}}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "balance"], returncode=0, stdout='{"ok": true, "data": {"totalValueUsd": "50.00"}}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "swap"], returncode=0, stdout='{"ok": true, "data": [{"routerResult": {"fromTokenAmount": "5000000"}, "tx": {"to": "0x3156020dfF8D99af1dDC523ebDfb1ad2018554a0", "data": "0xabcdef", "value": "0"}}]}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "check-approvals"], returncode=0, stdout='{"ok": true, "data": [{"tokens": [{"spendable": "0"}]}]}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "approve"], returncode=0, stdout='{"ok": true, "data": [{"data": "0xapprove", "gasLimit": "70000"}]}', stderr=""),
                subprocess.CompletedProcess(args=["gateway", "simulate"], returncode=0, stdout='{"ok": true, "data": [{"failReason": "", "risks": []}]}', stderr=""),
                subprocess.CompletedProcess(args=["wallet", "contract-call"], returncode=0, stdout='{"ok": true, "data": {"txHash": "0xap0"}}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "check-approvals"], returncode=0, stdout='{"ok": true, "data": [{"tokens": [{"spendable": "5000000"}]}]}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "execute"], returncode=0, stdout='{"ok": true, "data": {"approveTxHash": "0xap1", "swapTxHash": "0xsw1"}}', stderr=""),
                subprocess.CompletedProcess(args=["swap", "execute"], returncode=0, stdout='{"ok": true, "data": {"swapTxHash": "0xsw2"}}', stderr=""),
            ]
        )

        def _executor(command, **kwargs):
            calls.append(list(command))
            return next(responses)

        try:
            os.environ["OKX_API_KEY"] = "okx-ak"
            os.environ["OKX_SECRET_KEY"] = "okx-sk"
            os.environ["OKX_PASSPHRASE"] = "okx-pp"
            os.environ["OT_ONCHAINOS_CLI_BIN"] = "/bin/echo"
            result = run_live(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "AVE",
                    "target_token_address": "0x1111111111111111111111111111111111111111",
                    "execution_source_symbol": "USDT",
                    "execution_source_address": "0x55d398326f99059ff775485246999027b3197955",
                    "execution_source_readable_amount": 300,
                    "desired_notional_usd": 300,
                    "per_leg_usd": 150,
                    "leg_count": 2,
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "live_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": [],
                    "requires_explicit_approval": False,
                    "metadata": {"live_cap_usd": 10.0},
                },
                project_root=REPO_ROOT,
                executor=_executor,
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(result["execution_readiness"], "live_ready")
        self.assertEqual(result["live_cap_usd"], 10.0)
        self.assertEqual(result["executed_leg_count"], 2)
        self.assertEqual(result["prepared_execution"]["per_leg_usd"], 5.0)
        self.assertEqual(result["prepared_execution"]["execution_wallet_address"], "0x2222222222222222222222222222222222222222")
        self.assertEqual(result["tx_hashes"], ["0xap0", "0xap1", "0xsw1", "0xsw2"])
        self.assertIn("broadcast", result["approval_result"])
        swap_execute_calls = [item for item in calls if len(item) >= 3 and item[1] == "swap" and item[2] == "execute"]
        self.assertEqual(len(swap_execute_calls), 2)
        contract_call = [item for item in calls if len(item) >= 3 and item[1] == "wallet" and item[2] == "contract-call"]
        self.assertEqual(len(contract_call), 1)

    def test_vendored_onchainos_provenance_present(self) -> None:
        provenance = REPO_ROOT / "vendor" / "onchainos_cli" / "UPSTREAM.md"
        self.assertTrue(provenance.is_file())
        self.assertIn("onchainos-skills", provenance.read_text(encoding="utf-8"))

    def test_generated_skill_actions_split_network_permissions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-actions",
                    reflection_session_id="pi-session-actions",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_normalized_review("0xwallet4001", "bsc"))},
                    normalized_output=_mock_normalized_review("0xwallet4001", "bsc"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet4001", chain="bsc")
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            actions = yaml.safe_load((Path(result["promotion"]["package_root"]) / "actions.yaml").read_text(encoding="utf-8"))
            self.assertEqual(actions["default_action"], "primary")
            primary = next(item for item in actions["actions"] if item["id"] == "primary")
            execute = next(item for item in actions["actions"] if item["id"] == "execute")
            self.assertFalse(primary["allow_network"])
            self.assertTrue(execute["allow_network"])


if __name__ == "__main__":
    unittest.main()
