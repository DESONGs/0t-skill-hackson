from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest import mock
from types import SimpleNamespace

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
AVE_SERVICE_ROOT = REPO_ROOT / "services" / "ave-data-service"
for candidate in (SRC_ROOT, AVE_SERVICE_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from ot_skill_enterprise.control_plane.api import build_control_plane_api
from ot_skill_enterprise.chain_assets import chain_benchmark_defaults
from ot_skill_enterprise.execution import collect_execution_result, prepare_execution, run_dry_run, run_live
from ot_skill_enterprise.runtime import RuntimeExecutionRequest, RuntimeLaunchSpec
from ot_skill_enterprise.runtime.executor import SubprocessRuntimeExecutor
from ot_skill_enterprise.skills_compiler.wallet_style_runtime import build_primary_payload
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
from ot_skill_enterprise.style_distillation.service import (
    _fallback_execution_intent,
    _pick_focus_tokens,
    _preprocess_wallet_data,
    _try_salvage_from_raw_text,
    WalletStyleDistillationAttemptsExceeded,
    build_wallet_style_distillation_service,
)
from ot_skill_enterprise.style_distillation.models import ExecutionIntent
from ot_skill_enterprise.style_distillation.trade_pairing import compute_trade_statistics, pair_trades

from providers import AveRestProvider  # noqa: E402


def _mock_legacy_full_review(wallet: str, chain: str) -> dict[str, object]:
    # Compatibility-only fixture for the deprecated full reflection contract.
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
        "review": {
            "status": "generate",
            "should_generate_candidate": True,
            "reasoning": "The pattern is consistent enough for an MVP skill.",
            "nudge_prompt": "Generate the wallet style skill now.",
            "metadata": {"prompt_focus": ["risk"]},
        },
    }


def _generic_reflection_review(wallet: str, chain: str) -> dict[str, object]:
    payload = _mock_legacy_full_review(wallet, chain)
    payload["profile"] = {
        **dict(payload["profile"]),
        "wallet": "0x0000000000000000000000000000000000000000",
        "chain": "ethereum",
        "style_label": "balanced",
        "summary": "Balanced risk profile with moderate conviction.",
        "dominant_actions": [],
        "preferred_tokens": [],
        "active_windows": [],
        "execution_rules": [],
    }
    payload["strategy"] = {
        **dict(payload["strategy"]),
        "setup_label": "default",
        "summary": "Standard entry and exit strategy.",
        "entry_conditions": [
            {
                "condition": "price above support",
                "data_source": "onchain",
                "weight": 1.0,
                "rationale": "",
            }
        ],
    }
    return payload


def _mock_minimal_distill_review(wallet: str, chain: str, *, status: str = "generate") -> dict[str, object]:
    return {
        "wallet": wallet,
        "chain": chain,
        "summary": f"{wallet} on {chain} rotates aggressively into AVE when momentum strengthens.",
        "primary_archetype": "meme_hunter",
        "secondary_archetypes": ["dip_buyer"],
        "behavioral_patterns": ["fast_rotation", "profit_scaling"],
        "archetype_confidence": 0.74,
        "archetype_evidence_summary": ["Scales into strength", "Prefers fast rotations over passive holds"],
        "dominant_actions": ["swap", "buy"],
        "preferred_tokens": ["AVE", "USDT"],
        "active_windows": ["asia-open"],
        "risk_flags": ["block_if_security_scan_fails"],
        "setup_label": "momentum-rotation",
        "setup_summary": "Rotate into AVE when the regime is supportive and volume expands.",
        "entry_signals": ["entry_factor == 'volume_spike'"],
        "prompt_focus": ["risk", "timing"],
        "review_status": status,
        "reasoning": "The wallet shows a repeatable fast-rotation pattern with enough evidence for structured generation.",
        "nudge_prompt": "Assemble the wallet skill from this evidence.",
    }


def _minimal_preprocessed_wallet(wallet: str, chain: str) -> dict[str, object]:
    return {
        "wallet": wallet,
        "chain": chain,
        "wallet_summary": {
            "wallet_address": wallet,
            "chain": chain,
            "balance_usd": 10000,
            "total_balance_usd": 10000,
        },
        "focus_tokens": [
            {"symbol": "AVE", "token_address": "0x0000000000000000000000000000000000000ave"},
            {"symbol": "USDT", "token_address": "0x0000000000000000000000000000000000000usd"},
        ],
        "recent_activity": [
            {"timestamp": "2026-04-14T02:00:00Z", "symbol": "AVE"},
            {"timestamp": "2026-04-14T03:00:00Z", "symbol": "USDT"},
        ],
        "market_context": {"macro": {"market_regime": "risk_on"}},
        "signal_context": {
            "top_entry_factors": [
                {
                    "factor_type": "volume_spike",
                    "description": "Volume expanded on profitable entries.",
                    "confidence": 0.7,
                }
            ]
        },
        "behavioral_patterns": [
            {"pattern_label": "fast_rotation", "strength": 0.81, "evidence": ["Scales into strength"]}
        ],
        "archetype": {
            "primary_label": "meme_hunter",
            "secondary_archetypes": ["dip_buyer"],
            "confidence": 0.74,
            "evidence": ["Scales into strength"],
            "token_preference": ["AVE", "USDT"],
            "behavioral_patterns": [{"pattern_label": "fast_rotation", "strength": 0.81}],
        },
        "derived_stats": {
            "activity_count": 6,
            "preferred_tokens": ["AVE", "USDT"],
            "top_quote_tokens": ["USDT", "USDC"],
            "dominant_actions": ["swap", "buy"],
            "top_holding_allocation_pct": 41,
            "stablecoin_allocation_pct": 38,
            "avg_activity_usd": 2400,
            "focus_token_count": 2,
            "primary_archetype": "meme_hunter",
            "secondary_archetypes": ["dip_buyer"],
            "behavioral_patterns": ["fast_rotation", "profit_scaling"],
            "archetype_confidence": 0.74,
            "archetype_evidence_summary": ["Scales into strength", "Prefers fast rotations over passive holds"],
            "burst_profile": "staggered",
        },
    }


def _execution_intent_for_tests() -> ExecutionIntent:
    return ExecutionIntent(
        adapter="onchainos_cli",
        mode="dry_run_ready",
        preferred_workflow="swap_execute",
        preflight_checks=("security_token_scan",),
        route_preferences=("USDT", "USDC"),
        split_legs=True,
        leg_count=2,
        max_position_pct=12.0,
        requires_explicit_approval=True,
    )


class FakeReflectionService:
    def __init__(self, result: ReflectionJobResult) -> None:
        self.result = result
        self.seen_specs: list[ReflectionJobSpec] = []

    def run(self, spec: ReflectionJobSpec) -> ReflectionJobResult:
        self.seen_specs.append(spec)
        return self.result


class ScriptedReflectionService:
    def __init__(self, results: list[ReflectionJobResult]) -> None:
        if not results:
            raise ValueError("results must not be empty")
        self.results = list(results)
        self.seen_specs: list[ReflectionJobSpec] = []

    def run(self, spec: ReflectionJobSpec) -> ReflectionJobResult:
        self.seen_specs.append(spec)
        return self.results.pop(0)


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

    def test_ave_rest_provider_normalizes_risk_and_holder_shapes(self) -> None:
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
                        }
                    },
                },
                "risk": {
                    "status": 1,
                    "data": {
                        "ai_report": {
                            "mechanism_en": "Owner can restrict transfers.",
                            "summary": {
                                "risk_level": "low",
                                "has_freeze_mechanism": True,
                                "has_transfer_risk": True,
                                "has_mint_burn_risk": True,
                            },
                            "risk": [
                                {"name_en": "Transfer Restriction Mode"},
                                {"name_en": "Owner-Only Initialization and Mode Setting"},
                            ],
                        }
                    },
                },
                "holders": {
                    "status": 1,
                    "data": [
                        {"holder": "0xabc", "balance_ratio": 0.315, "balance_usd": 1000},
                        {"holder": "0xdef", "balance_ratio": 0.12, "balance_usd": 400},
                    ],
                },
            }
        )
        payload = type(
            "InspectTokenPayload",
            (),
            {
                "token_ref": type(
                    "TokenRef",
                    (),
                    {
                        "identifier": "bsc:0x1111111111111111111111111111111111111111",
                        "token_address": "0x1111111111111111111111111111111111111111",
                        "chain": "bsc",
                        "symbol": "ALPHA",
                        "name": "ALPHA",
                    },
                )(),
                "include_risk": True,
                "include_holders": True,
            },
        )()
        token = provider.inspect_token(payload)
        self.assertEqual(token["risk_snapshot"]["risk_level"], "low")
        self.assertIn("freeze_mechanism", token["risk_snapshot"]["flags"])
        self.assertIn("Transfer Restriction Mode", token["risk_snapshot"]["metadata"]["ai_risk_names"])
        self.assertEqual(token["holder_snapshot"]["holder_count"], 2)
        self.assertAlmostEqual(token["holder_snapshot"]["top_holder_share_pct"], 31.5)
        self.assertAlmostEqual(token["holder_snapshot"]["holders"][0]["share_pct"], 31.5)

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

    def test_risk_filters_map_ai_report_flags(self) -> None:
        filters = build_risk_filters(
            [
                {
                    "identity": {"symbol": "ALPHA"},
                    "risk_snapshot": {
                        "risk_level": "low",
                        "flags": ["freeze_mechanism", "transfer_restriction_mode"],
                        "metadata": {
                            "ai_report_summary": {
                                "has_freeze_mechanism": True,
                                "has_transfer_risk": True,
                                "has_mint_burn_risk": True,
                            },
                            "ai_risk_names": ["Transfer Restriction Mode", "Owner-Only Initialization and Mode Setting"],
                        },
                    },
                    "holder_snapshot": {"top_holder_share_pct": 31.5},
                }
            ]
        )
        filter_types = {item.filter_type for item in filters}
        self.assertIn("transfer_restriction", filter_types)
        self.assertIn("owner_transfer_control", filter_types)
        self.assertIn("mint_burn_risk", filter_types)
        self.assertIn("holder_concentration", filter_types)


class WalletStyleReflectionTests(unittest.TestCase):
    def test_parse_wallet_style_review_report_accepts_minimal_distill_output(self) -> None:
        report = parse_wallet_style_review_report(
            _mock_minimal_distill_review("0xabc", "solana"),
            wallet="0xabc",
            chain="solana",
            preprocessed=_minimal_preprocessed_wallet("0xabc", "solana"),
        )
        self.assertEqual(report.profile.wallet, "0xabc")
        self.assertEqual(report.profile.style_label, "meme_hunter")
        self.assertEqual(report.strategy.setup_label, "momentum-rotation")
        self.assertEqual(report.execution_intent.adapter, "onchainos_cli")
        self.assertEqual(report.review.status, "generate")
        self.assertFalse(report.normalized_output["metadata"].get("_auto_fixes"))

    def test_parse_wallet_style_review_report_accepts_legacy_full_output(self) -> None:
        payload = _mock_legacy_full_review("0xabc", "solana")
        report = parse_wallet_style_review_report(
            payload,
            wallet="0xabc",
            chain="solana",
            execution_intent=_execution_intent_for_tests(),
        )
        self.assertEqual(report.profile.wallet, "0xabc")
        self.assertEqual(report.profile.style_label, "balanced-position-holding")
        self.assertEqual(report.strategy.setup_label, "reserve-heavy-rotation")
        self.assertEqual(report.execution_intent.adapter, "onchainos_cli")
        self.assertTrue(report.review.should_generate_candidate)
        self.assertEqual(report.review.status, "generate")

    def test_parse_wallet_style_review_report_auto_fixes_wallet_chain(self) -> None:
        payload = _mock_legacy_full_review("0xABC", "ETH")
        report = parse_wallet_style_review_report(
            payload,
            wallet="0xabc",
            chain="solana",
            execution_intent=_execution_intent_for_tests(),
        )
        self.assertEqual(report.profile.wallet, "0xabc")
        self.assertEqual(report.profile.chain, "solana")
        metadata = report.normalized_output.get("metadata") or {}
        self.assertIn("_auto_fixes", metadata)
        self.assertTrue(any(item.get("field") == "profile.wallet" for item in metadata.get("_auto_fixes", [])))
        self.assertTrue(any(item.get("field") == "profile.chain" for item in metadata.get("_auto_fixes", [])))

    def test_parse_wallet_style_review_report_without_execution_intent_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_wallet_style_review_report(
                _mock_legacy_full_review("0xabc", "solana"),
                wallet="0xabc",
                chain="solana",
                execution_intent=None,
            )

    def test_parse_wallet_style_review_report_invalid_schema_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_wallet_style_review_report(
                {"profile": {}},
                wallet="0xabc",
                chain="solana",
                execution_intent=_execution_intent_for_tests(),
            )

    def test_parse_wallet_style_review_report_accepts_low_signal_with_synthetic_entry_condition(self) -> None:
        report = parse_wallet_style_review_report(
            _mock_minimal_distill_review("0xabc", "solana", status="insufficient_signal"),
            wallet="0xabc",
            chain="solana",
            preprocessed=_minimal_preprocessed_wallet("0xabc", "solana"),
        )
        self.assertEqual(report.review.status, "insufficient_signal")
        self.assertFalse(report.review.should_generate_candidate)
        self.assertEqual(len(report.strategy.entry_conditions), 1)
        self.assertEqual(report.strategy.entry_conditions[0].data_source, "reflection.review.status")
        self.assertTrue(report.strategy.entry_conditions[0].metadata.get("synthetic"))

    def test_parse_wallet_style_review_report_autofixes_missing_entry_condition_source(self) -> None:
        payload = _mock_legacy_full_review("0xabc", "solana")
        payload["strategy"]["entry_conditions"][0]["data_source"] = ""
        report = parse_wallet_style_review_report(
            payload,
            wallet="0xabc",
            chain="solana",
            execution_intent=_execution_intent_for_tests(),
        )
        self.assertEqual(report.strategy.entry_conditions[0].data_source, "reflection.strategy.entry_conditions")
        metadata = report.normalized_output.get("metadata") or {}
        self.assertTrue(
            any(
                item.get("field") == "strategy.entry_conditions[].data_source"
                for item in metadata.get("_auto_fixes", [])
            )
        )

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
            metadata={"mock_response": _mock_minimal_distill_review("0xreflect-context", "bsc")},
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

    def test_reflection_job_sets_request_timeout_and_token_budget_metadata(self) -> None:
        runtime_service = RecordingRuntimeService()
        service = PiReflectionService(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace", runtime_service=runtime_service)
        previous_timeout = os.environ.get("OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS")
        previous_tokens = os.environ.get("OT_PI_REFLECTION_MAX_TOKENS")
        os.environ["OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS"] = "75"
        os.environ["OT_PI_REFLECTION_MAX_TOKENS"] = "1600"
        try:
            spec = ReflectionJobSpec(
                subject_kind="wallet_style_reflection",
                subject_id="0xreflect-timeout",
                flow_id="wallet_style_reflection_review",
                system_prompt="Return the requested wallet style profile.",
                compact_input={"wallet": "0xreflect-timeout", "chain": "bsc"},
                expected_output_schema=build_wallet_style_output_schema(),
                artifact_root=REPO_ROOT / ".ot-workspace" / "artifacts",
                metadata={"mock_response": _mock_minimal_distill_review("0xreflect-timeout", "bsc")},
            )
            result = service.run(spec)
            self.assertEqual(result.status, "succeeded")
        finally:
            if previous_timeout is None:
                os.environ.pop("OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS", None)
            else:
                os.environ["OT_PI_REFLECTION_REQUEST_TIMEOUT_SECONDS"] = previous_timeout
            if previous_tokens is None:
                os.environ.pop("OT_PI_REFLECTION_MAX_TOKENS", None)
            else:
                os.environ["OT_PI_REFLECTION_MAX_TOKENS"] = previous_tokens
        call = runtime_service.calls[0]
        self.assertEqual(call["metadata"]["reflection_request_timeout_seconds"], 75.0)
        self.assertEqual(call["metadata"]["reflection_max_tokens"], 1600)

    def test_reflection_job_uses_higher_default_token_budget(self) -> None:
        runtime_service = RecordingRuntimeService()
        service = PiReflectionService(project_root=REPO_ROOT, workspace_root=REPO_ROOT / ".ot-workspace", runtime_service=runtime_service)
        previous_tokens = os.environ.get("OT_PI_REFLECTION_MAX_TOKENS")
        os.environ.pop("OT_PI_REFLECTION_MAX_TOKENS", None)
        try:
            spec = ReflectionJobSpec(
                subject_kind="wallet_style_reflection",
                subject_id="0xreflect-default-budget",
                flow_id="wallet_style_reflection_review",
                system_prompt="Return the requested wallet style profile.",
                compact_input={"wallet": "0xreflect-default-budget", "chain": "bsc"},
                expected_output_schema=build_wallet_style_output_schema(),
                artifact_root=REPO_ROOT / ".ot-workspace" / "artifacts",
                metadata={"mock_response": _mock_minimal_distill_review("0xreflect-default-budget", "bsc")},
            )
            result = service.run(spec)
            self.assertEqual(result.status, "succeeded")
        finally:
            if previous_tokens is None:
                os.environ.pop("OT_PI_REFLECTION_MAX_TOKENS", None)
            else:
                os.environ["OT_PI_REFLECTION_MAX_TOKENS"] = previous_tokens
        call = runtime_service.calls[0]
        self.assertEqual(call["metadata"]["reflection_max_tokens"], 3500)

    def test_chain_benchmark_source_defaults_are_chain_specific(self) -> None:
        ethereum_defaults = chain_benchmark_defaults("ethereum")
        base_defaults = chain_benchmark_defaults("base")
        bsc_defaults = chain_benchmark_defaults("bsc")
        polygon_defaults = chain_benchmark_defaults("polygon")
        self.assertEqual(ethereum_defaults["default_source_token"], "WETH")
        self.assertEqual(base_defaults["default_source_token"], "WETH")
        self.assertNotEqual(
            ethereum_defaults["default_source_token_address"],
            base_defaults["default_source_token_address"],
        )
        self.assertEqual(bsc_defaults["default_source_token"], "WBNB")
        self.assertEqual(
            bsc_defaults["default_source_token_address"],
            "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        )
        self.assertEqual(polygon_defaults["default_source_token"], "WPOL")

    def test_fallback_execution_intent_uses_chain_benchmark_source_defaults(self) -> None:
        intent = _fallback_execution_intent(
            {"chain": "ethereum", "derived_stats": {}},
            SimpleNamespace(position_sizing={}, preferred_setups=()),
        )
        self.assertEqual(intent.metadata["default_source_token"], "WETH")
        self.assertEqual(
            intent.metadata["default_source_token_address"],
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        )

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
                metadata={"mock_response": _mock_minimal_distill_review("0xreflect1", "bsc")},
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

    def test_reflection_mock_spec_uses_minimal_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / ".ot-workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            service = build_wallet_style_distillation_service(project_root=REPO_ROOT, workspace_root=workspace)
            previous_mock = os.environ.get("OT_PI_REFLECTION_MOCK")
            os.environ["OT_PI_REFLECTION_MOCK"] = "1"
            try:
                spec = service._build_reflection_spec(
                    wallet="0xmockminimal",
                    chain="bsc",
                    prompt="Return the requested wallet style profile.",
                    preprocessed=_minimal_preprocessed_wallet("0xmockminimal", "bsc"),
                    artifacts_dir=workspace / "artifacts",
                )
            finally:
                if previous_mock is None:
                    os.environ.pop("OT_PI_REFLECTION_MOCK", None)
                else:
                    os.environ["OT_PI_REFLECTION_MOCK"] = previous_mock
            mock_response = dict(spec.metadata.get("mock_response") or {})
            self.assertEqual(mock_response["wallet"], "0xmockminimal")
            self.assertEqual(mock_response["chain"], "bsc")
            self.assertEqual(mock_response["review_status"], "generate")
            self.assertEqual(mock_response["metadata"]["source_contract"], "minimal_mock")
            self.assertNotIn("profile", mock_response)
            self.assertNotIn("strategy", mock_response)
            self.assertNotIn("review", mock_response)

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
                    raw_output={"text": json.dumps(_mock_minimal_distill_review("0xwallet1001", "bsc"))},
                    normalized_output=_mock_minimal_distill_review("0xwallet1001", "bsc"),
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
            self.assertEqual(result["execution_readiness"], "blocked_by_risk")
            self.assertEqual(result["strategy_quality"], "insufficient_data")
            self.assertEqual(result["example_readiness"], "blocked_by_missing_features")
            self.assertTrue(reflection.seen_specs)
            self.assertNotIn("onchainos", json.dumps(reflection.seen_specs[0].compact_input))

            summary_path = Path(result["artifacts"]["job_root"]) / "summary.json"
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["review_backend"], "pi-reflection-mock")
            self.assertEqual(payload["summary"]["reflection_run_id"], "run-reflection-1")
            self.assertFalse(payload["summary"]["fallback_used"])
            self.assertEqual(payload["summary"]["execution_readiness"], "blocked_by_risk")
            execution_smoke = json.loads(Path(result["artifacts"]["execution_smoke_output"]).read_text(encoding="utf-8"))
            smoke_metadata = execution_smoke["parsed_output"]["metadata"]
            self.assertEqual(smoke_metadata.get("readiness_reason"), "missing_target_token_address")
            self.assertEqual(smoke_metadata.get("smoke_attempt_count"), 1)
            self.assertFalse(smoke_metadata.get("smoke_fallback_used"))
            self.assertEqual(smoke_metadata.get("smoke_initial_target"), "AVE")
            self.assertEqual(smoke_metadata.get("smoke_effective_target"), "AVE")
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

    def test_preprocess_hard_limits_compact_payload_budget(self) -> None:
        wallet_profile = {
            "wallet_summary": {
                "wallet_address": "0xabc",
                "chain": "bsc",
                "balance_usd": 1000,
                "total_balance_usd": 1000,
                "total_profit_ratio": 0.2,
                "total_win_ratio": 0.5,
                "total_purchase": 10,
                "total_sold": 9,
            },
            "holdings": [
                {
                    "token_ref": {
                        "symbol": f"TOKEN{i}",
                        "identifier": f"bsc:0x{i:040x}",
                        "token_address": f"0x{i:040x}",
                    },
                    "allocation_pct": 10,
                    "value_usd": 100,
                    "quantity": 1000,
                    "metadata": {"long_note": "x" * 500},
                }
                for i in range(8)
            ],
            "recent_activity": [
                {
                    "tx_hash": f"0x{i}",
                    "timestamp": "2026-04-14T00:00:00Z",
                    "action": "buy",
                    "amount_usd": 100 + i,
                    "token_ref": {
                        "symbol": f"TOKEN{i}",
                        "identifier": f"bsc:0x{i:040x}",
                        "token_address": f"0x{i:040x}",
                    },
                    "quote_symbol": "WBNB",
                    "note": "n" * 400,
                }
                for i in range(12)
            ],
        }
        token_profiles = [
            {
                "identity": {"symbol": f"TOKEN{i}", "token_address": f"0x{i:040x}", "identifier": f"bsc:0x{i:040x}"},
                "market_snapshot": {"liquidity_usd": 100000, "volume_24h_usd": 50000},
                "risk_snapshot": {"risk_level": "medium", "flags": ["freeze_mechanism"], "metadata": {"ai_report_summary": {"has_transfer_risk": True}}},
                "holder_snapshot": {"top_holder_share_pct": 33.3},
                "main_pair_ref": {"identifier": "TOKEN/WBNB"},
            }
            for i in range(6)
        ]
        preprocessed = _preprocess_wallet_data(
            wallet="0xabc",
            chain="bsc",
            wallet_profile=wallet_profile,
            token_profiles=token_profiles,
            signals={"signals": [{"title": "signal", "severity": "high", "note": "s" * 400}] * 8},
            focus_tokens=token_profiles,
            trade_statistics={"completed_trade_count": 20, "win_rate": 0.55, "profit_factor": 1.8, "avg_holding_seconds": 1200},
            market_contexts=[{"symbol": "TOKEN0", "price_change_1h_pct": 12, "price_change_24h_pct": 40, "momentum_label": "surging", "volatility_regime": "high", "volume_to_liquidity_ratio": 2.4, "liquidity_usd": 100000, "note": "m" * 400}] * 6,
            macro_context={"market_regime": "risk_on", "summary": "m" * 500},
            entry_factors=[{"factor_type": "volume_spike", "description": "d" * 300, "frequency": 0.6, "confidence": 0.7}] * 5,
            risk_filters=[{"filter_type": "transfer_restriction", "description": "r" * 300, "is_hard_block": True}] * 4,
            fetch_metadata={"parallel": True, "token_fetch_count": 6},
            derived_memory=[{"summary": "y" * 500, "payload": {"preferred_tokens": ["TOKEN0", "TOKEN1"], "active_windows": ["asia-open"]}}] * 4,
        )
        self.assertLessEqual(preprocessed["compact_size_bytes"], 6144)

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
                result = service.distill_wallet_style(wallet="0xwallet1002", chain="bsc", max_attempts=1)
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            self.assertTrue(result["fallback_used"])
            self.assertEqual(result["review_backend"], "wallet-style-extractor-fallback")
            self.assertEqual(result["reflection_run_id"], "run-reflection-bad")
            self.assertEqual(len(reflection.seen_specs), 3)
            style_review = json.loads(Path(result["artifacts"]["style_review"]).read_text(encoding="utf-8"))
            self.assertTrue(style_review["metadata"]["fallback_used"])
            reflection_result = json.loads(Path(result["artifacts"]["reflection_result"]).read_text(encoding="utf-8"))
            self.assertEqual(reflection_result["status"], "succeeded")

    def test_wallet_style_reflection_salvages_truncated_raw_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            previous_provider = os.environ.get("AVE_DATA_PROVIDER")
            os.environ["AVE_DATA_PROVIDER"] = "mock"
            provider = build_ave_provider()
            raw_review = json.dumps(_mock_minimal_distill_review("0xwallet9001", "bsc"))
            truncated_raw_review = raw_review[:-1]
            salvage_result = ReflectionJobResult(
                review_backend="pi-reflection-agent:openai/gpt-5.4",
                reflection_run_id="run-reflection-raw-salvage",
                reflection_session_id="pi-session-raw-salvage",
                status="succeeded",
                raw_output={"text": truncated_raw_review, "raw_text": truncated_raw_review},
                normalized_output={"unexpected": True},
                fallback_used=False,
            )
            reflection = ScriptedReflectionService([salvage_result])
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet9001", chain="bsc", max_attempts=1)
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            self.assertFalse(result["fallback_used"])
            self.assertEqual(result["review_backend"], "pi-reflection-agent:openai/gpt-5.4")
            self.assertEqual(len(reflection.seen_specs), 1)
            reflection_result = json.loads(Path(result["artifacts"]["reflection_result"]).read_text(encoding="utf-8"))
            self.assertTrue(reflection_result["metadata"]["raw_text_salvaged"])
            style_review = json.loads(Path(result["artifacts"]["style_review"]).read_text(encoding="utf-8"))
            self.assertFalse(style_review["metadata"]["fallback_used"])
            self.assertEqual(result["execution_intent"]["adapter"], "onchainos_cli")

    def test_try_salvage_from_raw_text_repairs_truncated_arrays(self) -> None:
        salvaged = _try_salvage_from_raw_text(
            {
                "raw_text": '{"profile":{"wallet":"0xabc","chain":"bsc","dominant_actions":["swap","transfer"]',
            }
        )
        self.assertEqual(
            salvaged,
            {
                "profile": {
                    "wallet": "0xabc",
                    "chain": "bsc",
                    "dominant_actions": ["swap", "transfer"],
                }
            },
        )

    def test_parse_wallet_style_review_report_rejects_generic_outputs(self) -> None:
        with self.assertRaises(ValueError):
            parse_wallet_style_review_report(
                _generic_reflection_review("0xwallet1003", "bsc"),
                wallet="0xwallet1003",
                chain="bsc",
                execution_intent=_execution_intent_for_tests(),
            )

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
                    raw_output={"text": json.dumps(_mock_minimal_distill_review("0xwallet2001", "bsc"))},
                    normalized_output=_mock_minimal_distill_review("0xwallet2001", "bsc"),
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
            self.assertEqual(result["review_backend"], "pi-reflection-mock")
            self.assertIn(result["execution_readiness"], {"blocked_by_risk", "blocked_by_config"})
            warnings = json.loads(Path(result["artifacts"]["token_enrichment_warnings"]).read_text(encoding="utf-8"))
            self.assertTrue(warnings)
            preprocessed = json.loads(Path(result["artifacts"]["preprocessed_wallet"]).read_text(encoding="utf-8"))
            self.assertEqual(preprocessed["enrichment"]["token_profile_count"], 0)
            self.assertEqual(preprocessed["derived_stats"]["enrich_warning_count"], len(warnings))
            execution_smoke = json.loads(Path(result["artifacts"]["execution_smoke_output"]).read_text(encoding="utf-8"))
            smoke_metadata = execution_smoke["parsed_output"]["metadata"]
            self.assertIn(
                smoke_metadata.get("readiness_reason"),
                {"okx_credentials_required_for_verification", "missing_target_token_address"},
            )
            self.assertEqual(smoke_metadata.get("smoke_attempt_count"), 1)
            self.assertFalse(smoke_metadata.get("smoke_fallback_used"))

    def test_generated_skill_outputs_trade_plan(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            (project_root / "skills").mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            provider = TokenEnrichFailProvider()
            reflection_payload = _mock_legacy_full_review("0xwallet3001", "bsc")
            reflection_payload["profile"] = {
                **dict(reflection_payload["profile"]),
                "summary": "0xwallet3001 on bsc rotates into ALPHA when momentum is constructive.",
                "preferred_tokens": ["ALPHA", "WBNB"],
                "execution_rules": ["Prefer ALPHA/WBNB rotations.", "Keep reserve-heavy posture."],
            }
            reflection_payload["strategy"] = {
                **dict(reflection_payload["strategy"]),
                "summary": "Rotate from wrapped native into ALPHA when the market is constructive.",
                "entry_conditions": [
                    {
                        "condition": "market_bias in ['bullish','range'] and candidate_token == 'ALPHA'",
                        "data_source": "ave.compact_input.derived_stats",
                        "weight": 0.82,
                        "rationale": "Follow the preferred token rotation.",
                    }
                ],
                "preferred_setups": ["ALPHA"],
            }
            reflection = FakeReflectionService(
                ReflectionJobResult(
                    review_backend="pi-reflection-mock",
                    reflection_run_id="run-reflection-trade-plan",
                    reflection_session_id="pi-session-trade-plan",
                    status="succeeded",
                    raw_output={"text": json.dumps(reflection_payload)},
                    normalized_output=reflection_payload,
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            result = service.distill_wallet_style(wallet="0xwallet3001", chain="bsc")
            script_path = Path(result["promotion"]["package_root"]) / "scripts" / "primary.py"
            bullish_context = {
                "market_bias": "bullish",
                "candidate_tokens": ["ALPHA"],
                "available_routes": ["WBNB"],
                "desired_notional_usd": 900,
                "burst_profile": "short-burst",
                "market_context": {"macro": {"regime": "risk_on"}, "focus_token_context": [{"symbol": "ALPHA", "price_1h_pct": 12.0, "vol_liq_ratio": 2.1}]},
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
            self.assertEqual(payload["trade_plan"]["target_token"], "ALPHA")
            self.assertTrue(payload["trade_plan"]["target_token_address"].startswith("0x"))
            self.assertEqual(payload["trade_plan"]["target_token_resolution"], "runtime_context")
            self.assertNotIn("default_target_token", payload["execution_intent"]["metadata"])
            self.assertNotIn("default_target_token_address", payload["execution_intent"]["metadata"])
            self.assertEqual(payload["execution_intent"]["metadata"]["default_source_token"], "WBNB")
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
            self.assertEqual(execute_payload["execution_readiness"], "blocked_by_config")
            self.assertEqual(execute_payload["metadata"]["verification_status"], "not_executed")
            self.assertEqual(
                execute_payload["metadata"]["configuration_required"],
                ["OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"],
            )
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
                    raw_output={"text": json.dumps(_mock_minimal_distill_review("0xwallet5001", "bsc"))},
                    normalized_output=_mock_minimal_distill_review("0xwallet5001", "bsc"),
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
                self.assertEqual(dry_snapshot["execution_readiness"], "blocked_by_risk")
                dry_metadata = dry_snapshot["dry_run"]["parsed_output"]["metadata"]
                self.assertEqual(dry_metadata.get("readiness_reason"), "missing_target_token_address")
                self.assertEqual(dry_metadata.get("smoke_attempt_count"), 1)
                self.assertFalse(dry_metadata.get("smoke_fallback_used"))

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
            self.assertEqual(live_snapshot["dry_run"]["execution_readiness"], "blocked_by_risk")
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

    def test_primary_payload_resolves_unseen_token_from_runtime_market_context(self) -> None:
        defaults = chain_benchmark_defaults("bsc")
        payload = build_primary_payload(
            summary="Aggressive BSC burst scalper.",
            profile={
                "wallet": "0xwallet-runtime",
                "chain": "bsc",
                "summary": "Aggressive BSC burst scalper.",
                "confidence": 0.7,
                "style_label": "Burst Scalper",
                "execution_tempo": "same-minute-burst",
                "risk_appetite": "aggressive-memecoin",
                "conviction_profile": "momentum-layered",
                "stablecoin_bias": "zero-stablecoin-deployment",
                "dominant_actions": ["buy", "sell"],
                "preferred_tokens": ["PP", "PIZZA"],
                "active_windows": ["us-session"],
                "execution_rules": ["Enter on volume spike", "Use WBNB routing"],
                "anti_patterns": [],
            },
            strategy={
                "summary": "Follow momentum on BSC microcaps.",
                "entry_conditions": [{"condition": "Volume spike present", "data_source": "signal_context.top_entry_factors", "weight": 0.8}],
                "position_sizing": {"median_usd": 240, "legs": 2},
                "preferred_setups": ["volume-spike microcap scalp"],
                "metadata": {},
            },
            execution_intent={
                "adapter": "onchainos_cli",
                "mode": "dry_run_ready",
                "preferred_workflow": "swap_execute",
                "preflight_checks": ["security_token_scan"],
                "route_preferences": ["WBNB"],
                "leg_count": 2,
                "requires_explicit_approval": True,
                "metadata": {
                    "chain": "bsc",
                    "default_source_token": defaults["default_source_token"],
                    "default_source_token_address": defaults["default_source_token_address"],
                    "default_source_unit_price_usd": defaults["default_source_unit_price_usd"],
                },
            },
            token_catalog={},
            context={
                "market_bias": "bullish",
                "market_context": {
                    "macro": {"regime": "risk_on"},
                    "focus_token_context": [
                        {
                            "symbol": "ALPHA",
                            "token_address": "0x1111111111111111111111111111111111111111",
                            "price_now": 0.12,
                            "price_1h_pct": 18.4,
                            "vol_liq_ratio": 2.8,
                        }
                    ],
                },
                "signal_context": {"top_entry_factors": [{"factor_type": "volume_spike"}], "hard_blocks": [], "warnings": []},
            },
        )
        self.assertEqual(payload["trade_plan"]["target_token"], "ALPHA")
        self.assertEqual(payload["trade_plan"]["target_token_address"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(payload["trade_plan"]["target_token_resolution"], "runtime_context")
        self.assertTrue(payload["execution_intent"]["metadata"]["market_discovery"]["enabled"])
        self.assertIn("ALPHA", payload["trade_plan"]["candidate_tokens"])

    def test_prepare_execution_resolves_explicit_new_token_via_search(self) -> None:
        defaults = chain_benchmark_defaults("bsc")
        previous = {key: os.environ.get(key) for key in ("AVE_API_KEY", "API_PLAN")}

        def _executor(command, **kwargs):
            if "token" in command and "search" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "data": [
                                {
                                    "chainIndex": "56",
                                    "tokenSymbol": "GENIUS",
                                    "tokenContractAddress": "0x2222222222222222222222222222222222222222",
                                    "price": "0.52",
                                    "change": "11.4",
                                    "liquidity": "1800000",
                                    "marketCap": "520000000",
                                }
                            ],
                        }
                    ),
                    stderr="",
                )
            if "token" in command and "price-info" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "data": [
                                {
                                    "chainIndex": "56",
                                    "tokenContractAddress": "0x2222222222222222222222222222222222222222",
                                    "price": "0.55",
                                    "priceChange1H": "7.2",
                                    "priceChange24H": "13.9",
                                    "liquidity": "2100000",
                                    "volume24H": "1050000",
                                    "txs24H": "5140",
                                }
                            ],
                        }
                    ),
                    stderr="",
                )
            if "watch-price" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "type": "price",
                            "token_id": "0x2222222222222222222222222222222222222222-bsc",
                            "price": 0.57,
                            "price_change_5m": 1.1,
                            "price_change_1h": 8.4,
                            "time": 1710000000,
                        }
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {command}")

        try:
            os.environ["AVE_API_KEY"] = "ave-test"
            os.environ["API_PLAN"] = "pro"
            prepared = prepare_execution(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "GENIUS",
                    "target_token_address": "",
                    "target_token_resolution": "market_search_pending",
                    "requested_target_token": "GENIUS",
                    "execution_source_symbol": defaults["default_source_token"],
                    "execution_source_address": defaults["default_source_token_address"],
                    "execution_source_unit_price_usd": defaults["default_source_unit_price_usd"],
                    "per_leg_usd": 120,
                    "leg_count": 2,
                    "market_discovery": {
                        "enabled": True,
                        "allow_target_override": False,
                        "wss_price_enabled": True,
                        "filters": {"chain": "bsc"},
                    },
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": ["security_token_scan"],
                    "route_preferences": ["WBNB"],
                    "leg_count": 2,
                    "requires_explicit_approval": True,
                    "metadata": {"chain": "bsc"},
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
        self.assertEqual(prepared["target_token"], "GENIUS")
        self.assertEqual(prepared["target_token_address"], "0x2222222222222222222222222222222222222222")
        self.assertEqual(prepared["resolved_trade_plan"]["target_token_resolution"], "market_discovery")
        self.assertEqual(prepared["market_stream_snapshot"]["source"], "ave_wss_price")

    def test_prepare_execution_can_override_static_watchlist_target_with_market_scan(self) -> None:
        defaults = chain_benchmark_defaults("bsc")
        previous = {key: os.environ.get(key) for key in ("AVE_API_KEY", "API_PLAN")}

        def _executor(command, **kwargs):
            if "token" in command and "hot-tokens" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "data": [
                                {
                                    "chainIndex": "56",
                                    "tokenSymbol": "PP",
                                    "tokenContractAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                    "change": "1.2",
                                    "liquidity": "100000",
                                    "volume": "90000",
                                    "txs": "120",
                                    "uniqueTraders": "40",
                                    "riskLevelControl": "1",
                                },
                                {
                                    "chainIndex": "56",
                                    "tokenSymbol": "GENIUS",
                                    "tokenContractAddress": "0x3333333333333333333333333333333333333333",
                                    "change": "12.5",
                                    "liquidity": "2400000",
                                    "volume": "1075000",
                                    "txs": "5140",
                                    "uniqueTraders": "803",
                                    "riskLevelControl": "1",
                                },
                            ],
                        }
                    ),
                    stderr="",
                )
            if "token" in command and "price-info" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ok": True,
                            "data": [
                                {
                                    "chainIndex": "56",
                                    "tokenContractAddress": "0x3333333333333333333333333333333333333333",
                                    "price": "0.54",
                                    "priceChange1H": "9.1",
                                    "priceChange24H": "13.45",
                                    "liquidity": "2131644.83",
                                    "volume24H": "1075754.22",
                                    "txs24H": "5140",
                                }
                            ],
                        }
                    ),
                    stderr="",
                )
            if "watch-price" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "type": "price",
                            "token_id": "0x3333333333333333333333333333333333333333-bsc",
                            "price": 0.56,
                            "price_change_5m": 0.9,
                            "price_change_1h": 9.8,
                            "time": 1710000001,
                        }
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {command}")

        try:
            os.environ["AVE_API_KEY"] = "ave-test"
            os.environ["API_PLAN"] = "pro"
            prepared = prepare_execution(
                {
                    "chain": "bsc",
                    "wallet_address": "0xd5b63edd7cdf4c23718cc8a6a83e312dc8ae3fe1",
                    "target_token": "PP",
                    "target_token_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "target_token_resolution": "style_watchlist_candidate",
                    "requested_target_token": "PP",
                    "execution_source_symbol": defaults["default_source_token"],
                    "execution_source_address": defaults["default_source_token_address"],
                    "execution_source_unit_price_usd": defaults["default_source_unit_price_usd"],
                    "per_leg_usd": 160,
                    "leg_count": 2,
                    "historical_tokens": ["PP", "XMONEY", "PIZZA"],
                    "market_discovery": {
                        "enabled": True,
                        "allow_target_override": True,
                        "novelty_preferred": True,
                        "wss_price_enabled": True,
                        "filters": {
                            "chain": "bsc",
                            "volume_min": 10000,
                            "liquidity_min": 5000,
                            "risk_filter": False,
                            "stable_token_filter": True,
                        },
                    },
                },
                {
                    "adapter": "onchainos_cli",
                    "mode": "dry_run_ready",
                    "preferred_workflow": "swap_execute",
                    "preflight_checks": ["security_token_scan"],
                    "route_preferences": ["WBNB"],
                    "leg_count": 2,
                    "requires_explicit_approval": True,
                    "metadata": {"chain": "bsc"},
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
        self.assertEqual(prepared["target_token"], "GENIUS")
        self.assertEqual(prepared["resolved_trade_plan"]["target_token"], "GENIUS")
        self.assertEqual(prepared["resolved_trade_plan"]["target_token_address"], "0x3333333333333333333333333333333333333333")
        self.assertEqual(prepared["resolved_trade_plan"]["target_token_resolution"], "market_discovery")

    def test_collect_execution_result_prepare_only_requires_okx_credentials(self) -> None:
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
        result = collect_execution_result(prepared, mode="prepare_only")
        self.assertTrue(result["ok"])
        self.assertEqual(result["execution_readiness"], "blocked_by_config")
        self.assertEqual(result["metadata"]["verification_status"], "not_executed")
        self.assertEqual(
            result["metadata"]["configuration_required"],
            ["OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"],
        )

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
                    raw_output={"text": json.dumps(_mock_minimal_distill_review("0xwallet4001", "bsc"))},
                    normalized_output=_mock_minimal_distill_review("0xwallet4001", "bsc"),
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

    def test_distill_wallet_style_retries_until_success(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / ".ot-workspace"
            service = build_wallet_style_distillation_service(project_root=REPO_ROOT, workspace_root=workspace)
            attempt_counter = {"value": 0}

            def _resume(job_id, *, live_execute=False, approval_granted=False):
                attempt_counter["value"] += 1
                job_dir = service._job_dir(job_id)
                service.ledger_store.on_stage_start(job_dir, stage="distill_features", summary=f"attempt {attempt_counter['value']}")
                if attempt_counter["value"] < 3:
                    service.ledger_store.on_stage_fail(job_dir, stage="distill_features", summary=f"failure {attempt_counter['value']}")
                    raise RuntimeError(f"failure {attempt_counter['value']}")
                service.ledger_store.on_stage_success(job_dir, stage="distill_features", summary="recovered")
                return {
                    "job_id": job_id,
                    "status": "succeeded",
                    "stage_statuses": service.ledger_store.load(job_dir).get("stage_statuses"),
                }

            service.resume_job = _resume  # type: ignore[method-assign]
            result = service.distill_wallet_style(wallet="0xwalletretry1", chain="bsc", max_attempts=3)
            self.assertEqual(result["attempt_report"]["attempt_count"], 3)
            self.assertEqual(len(result["attempt_report"]["attempts"]), 3)
            self.assertEqual(result["attempt_report"]["attempts"][0]["status"], "failed")
            self.assertEqual(result["attempt_report"]["attempts"][1]["status"], "failed")
            self.assertEqual(result["attempt_report"]["attempts"][2]["status"], "succeeded")

    def test_distill_wallet_style_stops_after_three_attempts_with_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / ".ot-workspace"
            service = build_wallet_style_distillation_service(project_root=REPO_ROOT, workspace_root=workspace)

            def _resume(job_id, *, live_execute=False, approval_granted=False):
                job_dir = service._job_dir(job_id)
                service.ledger_store.on_stage_start(job_dir, stage="skill_build", summary="attempt build")
                service.ledger_store.on_stage_fail(job_dir, stage="skill_build", summary=f"failure {job_id}")
                raise RuntimeError(f"failure {job_id}")

            service.resume_job = _resume  # type: ignore[method-assign]
            with self.assertRaises(WalletStyleDistillationAttemptsExceeded) as exc_info:
                service.distill_wallet_style(wallet="0xwalletretry2", chain="bsc", max_attempts=5)
            report = exc_info.exception.report
            self.assertEqual(report["max_attempts"], 3)
            self.assertEqual(report["attempt_count"], 3)
            self.assertEqual(len(report["attempts"]), 3)
            self.assertTrue(Path(report["report_path"]).is_file())
            self.assertEqual(report["attempts"][-1]["failed_stage"], "skill_build")

    def test_generic_reflection_output_is_normalized_locally(self) -> None:
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
                    review_backend="pi-reflection-agent:kimi-coding/kimi-k2-thinking",
                    reflection_run_id="run-reflection-hydrate",
                    reflection_session_id="pi-session-hydrate",
                    status="succeeded",
                    raw_output={"text": json.dumps(_generic_reflection_review("0xwallet7001", "bsc"))},
                    normalized_output=_generic_reflection_review("0xwallet7001", "bsc"),
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
                result = service.distill_wallet_style(wallet="0xwallet7001", chain="bsc", max_attempts=1)
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider
            self.assertFalse(result["fallback_used"])
            self.assertEqual(result["review_backend"], "pi-reflection-agent:kimi-coding/kimi-k2-thinking")
            self.assertEqual(result["profile"]["wallet"], "0xwallet7001")
            self.assertEqual(result["profile"]["chain"], "bsc")
            self.assertNotEqual(result["profile"]["style_label"], "balanced")
            self.assertNotEqual(result["strategy"]["setup_label"], "default")
            self.assertEqual(result["execution_intent"]["adapter"], "onchainos_cli")
            self.assertEqual(result["execution_intent"]["metadata"]["chain"], "bsc")
            reflection_result = json.loads(Path(result["artifacts"]["reflection_result"]).read_text(encoding="utf-8"))
            self.assertEqual(reflection_result["normalized_output"]["metadata"]["reflection_contract"], "legacy_full_normalized")

    def test_low_signal_reflection_skips_candidate_build_and_execution(self) -> None:
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
                    reflection_run_id="run-reflection-low-signal",
                    reflection_session_id="pi-session-low-signal",
                    status="succeeded",
                    raw_output={"text": json.dumps(_mock_minimal_distill_review("0xwallet8010", "bsc", status="insufficient_signal"))},
                    normalized_output=_mock_minimal_distill_review("0xwallet8010", "bsc", status="insufficient_signal"),
                    fallback_used=False,
                )
            )
            service = build_wallet_style_distillation_service(
                project_root=project_root,
                workspace_root=workspace,
                provider=provider,
                reflection_service=reflection,
            )
            compile_calls = {"count": 0}

            def _unexpected_compile(*args, **kwargs):  # noqa: ANN002, ANN003
                compile_calls["count"] += 1
                raise AssertionError("compile_candidate should not be called for low-signal review states")

            service.candidate_service = SimpleNamespace(  # type: ignore[assignment]
                compile_candidate=_unexpected_compile,
                validate_candidate=_unexpected_compile,
                promote_candidate=_unexpected_compile,
            )
            try:
                result = service.distill_wallet_style(wallet="0xwallet8010", chain="bsc", max_attempts=1)
            finally:
                if previous_provider is None:
                    os.environ.pop("AVE_DATA_PROVIDER", None)
                else:
                    os.environ["AVE_DATA_PROVIDER"] = previous_provider

            self.assertEqual(compile_calls["count"], 0)
            self.assertEqual(result["status"], "warn")
            self.assertEqual(result["review"]["status"], "insufficient_signal")
            self.assertFalse(result["review"]["should_generate_candidate"])
            self.assertIsNone(result["candidate"]["candidate_id"])
            self.assertIsNone(result["promotion"]["package_root"])
            self.assertEqual(result["execution_readiness"], "blocked_by_review_status")
            self.assertEqual(result["example_readiness"], "insufficient_signal")
            self.assertEqual(result["qa"]["status"], "warn")

    def test_execution_smoke_falls_back_to_next_candidate_when_default_target_unresolved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            promoted_root = project_root / "skills" / "wallet-style-smoke"
            (promoted_root / "scripts").mkdir(parents=True, exist_ok=True)
            (promoted_root / "scripts" / "execute.py").write_text("print('stub')\n", encoding="utf-8")
            workspace.mkdir(parents=True, exist_ok=True)
            service = build_wallet_style_distillation_service(project_root=project_root, workspace_root=workspace)

            primary_smoke_result = {
                "parsed_output": {
                    "trade_plan": {
                        "target_token": "PPAI",
                        "candidate_tokens": ["PPAI", "GENIUS", "RAVE"],
                        "historical_tokens": ["PPAI", "GENIUS"],
                        "execution_source_symbol": "WBNB",
                    },
                    "input_context": {
                        "candidate_tokens": ["GENIUS", "RAVE"],
                        "preferred_tokens": ["GENIUS"],
                    },
                }
            }
            attempted_targets: list[str] = []

            def _run_script_process(script_path: Path, payload: dict[str, object]) -> dict[str, object]:
                target = str(dict(payload.get("trade_plan") or {}).get("target_token") or "")
                attempted_targets.append(target)
                if target == "PPAI":
                    parsed_output = {
                        "summary": "default target unresolved",
                        "execution_readiness": "blocked_by_risk",
                        "metadata": {
                            "readiness_reason": "missing_target_token_address",
                            "readiness_detail": "trade_plan.target_token_address must be a valid EVM address (no_market_candidate)",
                        },
                    }
                    return {
                        "ok": False,
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "unresolved",
                        "parsed_output": parsed_output,
                    }
                parsed_output = {
                    "summary": f"{target} ready",
                    "execution_readiness": "dry_run_ready",
                    "metadata": {"resolved_target": {"symbol": target}},
                }
                return {
                    "ok": True,
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "parsed_output": parsed_output,
                }

            def _run_primary_context(promoted_root: Path, context: dict[str, object]) -> dict[str, object]:
                target = str(context.get("target_token") or "")
                return {
                    "ok": True,
                    "parsed_output": {
                        "trade_plan": {
                            "target_token": target,
                            "candidate_tokens": ["PPAI", "GENIUS", "RAVE"],
                            "historical_tokens": ["PPAI", "GENIUS"],
                            "execution_source_symbol": "WBNB",
                        }
                    },
                }

            service._run_script_process = _run_script_process  # type: ignore[method-assign]
            service._run_primary_context = _run_primary_context  # type: ignore[method-assign]

            result = service._execution_smoke_test(promoted_root, primary_smoke_result, {"adapter": "onchainos_cli"})

            self.assertEqual(attempted_targets, ["PPAI", "GENIUS"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["execution_readiness"], "dry_run_ready")
            self.assertIn("switched to GENIUS", result["summary"])
            self.assertEqual(result["parsed_output"]["metadata"]["smoke_attempt_count"], 2)
            self.assertTrue(result["parsed_output"]["metadata"]["smoke_fallback_used"])
            self.assertEqual(
                [item["target_token"] for item in result["parsed_output"]["metadata"]["smoke_attempts"]],
                ["PPAI", "GENIUS"],
            )

    def test_execution_smoke_stops_after_three_total_target_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            promoted_root = project_root / "skills" / "wallet-style-smoke"
            (promoted_root / "scripts").mkdir(parents=True, exist_ok=True)
            (promoted_root / "scripts" / "execute.py").write_text("print('stub')\n", encoding="utf-8")
            workspace.mkdir(parents=True, exist_ok=True)
            service = build_wallet_style_distillation_service(project_root=project_root, workspace_root=workspace)

            primary_smoke_result = {
                "parsed_output": {
                    "trade_plan": {
                        "target_token": "PPAI",
                        "candidate_tokens": ["PPAI", "GENIUS", "RAVE", "ARIA"],
                        "historical_tokens": ["PPAI", "GENIUS", "RAVE"],
                        "execution_source_symbol": "WBNB",
                    },
                    "input_context": {
                        "candidate_tokens": ["GENIUS", "RAVE", "ARIA"],
                        "preferred_tokens": ["GENIUS", "RAVE"],
                    },
                }
            }
            attempted_targets: list[str] = []

            def _run_script_process(script_path: Path, payload: dict[str, object]) -> dict[str, object]:
                target = str(dict(payload.get("trade_plan") or {}).get("target_token") or "")
                attempted_targets.append(target)
                parsed_output = {
                    "summary": f"{target} unresolved",
                    "execution_readiness": "blocked_by_risk",
                    "metadata": {
                        "readiness_reason": "missing_target_token_address",
                        "readiness_detail": "trade_plan.target_token_address must be a valid EVM address (no_market_candidate)",
                    },
                }
                return {
                    "ok": False,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "unresolved",
                    "parsed_output": parsed_output,
                }

            def _run_primary_context(promoted_root: Path, context: dict[str, object]) -> dict[str, object]:
                target = str(context.get("target_token") or "")
                return {
                    "ok": True,
                    "parsed_output": {
                        "trade_plan": {
                            "target_token": target,
                            "candidate_tokens": ["PPAI", "GENIUS", "RAVE", "ARIA"],
                            "historical_tokens": ["PPAI", "GENIUS", "RAVE"],
                            "execution_source_symbol": "WBNB",
                        }
                    },
                }

            service._run_script_process = _run_script_process  # type: ignore[method-assign]
            service._run_primary_context = _run_primary_context  # type: ignore[method-assign]

            result = service._execution_smoke_test(promoted_root, primary_smoke_result, {"adapter": "onchainos_cli"})

            self.assertEqual(attempted_targets, ["PPAI", "GENIUS", "RAVE"])
            self.assertFalse(result["ok"])
            self.assertEqual(result["execution_readiness"], "blocked_by_risk")
            self.assertIn("failed after 3 target attempts", result["summary"])
            self.assertEqual(result["parsed_output"]["metadata"]["smoke_attempt_count"], 3)
            self.assertEqual(
                [item["target_token"] for item in result["parsed_output"]["metadata"]["smoke_attempts"]],
                ["PPAI", "GENIUS", "RAVE"],
            )


class TestReflectionRuntimeFailureTaxonomy(unittest.TestCase):
    def test_subprocess_runtime_executor_classifies_timeout_and_parse_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            request = RuntimeExecutionRequest(
                runtime_id="pi",
                session_id="session-1",
                invocation_id="invocation-1",
                workspace_dir=str(temp_path / "workspace"),
                session_workspace=str(temp_path / "workspace" / "runtime-sessions" / "session-1"),
                cwd=str(temp_path),
                prompt="run reflection",
                input_payload={},
                metadata={},
                launch_spec=RuntimeLaunchSpec(runtime_id="pi", launcher=["python3", "-c", "print('ok')"]),
            )
            executor = SubprocessRuntimeExecutor()

            with mock.patch("ot_skill_enterprise.runtime.executor.subprocess.run") as run_mock:
                run_mock.side_effect = subprocess.TimeoutExpired(cmd=["python3"], timeout=1.0, stderr="timed out")
                timeout_result = executor.execute(request)

            self.assertEqual(timeout_result.error.details["failure_type"], "runtime_timeout")
            self.assertEqual(timeout_result.transcript.metadata["failure_type"], "runtime_timeout")
            self.assertEqual(timeout_result.transcript.output_payload["failure_type"], "runtime_timeout")

            with mock.patch("ot_skill_enterprise.runtime.executor.subprocess.run") as run_mock:
                run_mock.return_value = SimpleNamespace(returncode=0, stdout="not json", stderr="")
                parse_result = executor.execute(request)

            self.assertEqual(parse_result.error.code, "runtime_stdout_parse_failed")
            self.assertEqual(parse_result.error.details["failure_type"], "json_parse_failed")
            self.assertEqual(parse_result.transcript.metadata["failure_type"], "json_parse_failed")
            self.assertEqual(parse_result.transcript.output_payload["failure_type"], "json_parse_failed")

    def test_reflection_service_surfaces_runtime_failure_metadata(self) -> None:
        class FakeRunResult:
            def __init__(self) -> None:
                attempts = [
                    {
                        "attempt_index": 1,
                        "provider": "openai",
                        "model_id": "gpt-5.4",
                        "model": "openai/gpt-5.4",
                        "failure_type": "provider_unavailable",
                        "error": "provider unavailable",
                    },
                    {
                        "attempt_index": 2,
                        "provider": "openai",
                        "model_id": "gpt-5.4-mini",
                        "model": "openai/gpt-5.4-mini",
                        "failure_type": "runtime_timeout",
                        "error": "timed out",
                        "raw_text": "",
                        "raw_text_salvaged": False,
                    },
                ]
                self.transcript = SimpleNamespace(
                    ok=False,
                    status="failed",
                    summary="runtime process timed out after 180s",
                    output_payload={
                        "failure_type": "runtime_timeout",
                        "review_backend": "pi-reflection-agent:openai/gpt-5.4-mini",
                        "raw_output": {
                            "failure_type": "runtime_timeout",
                            "provider": "openai",
                            "model_id": "gpt-5.4-mini",
                            "model": "openai/gpt-5.4-mini",
                            "raw_text": "",
                            "raw_text_salvaged": False,
                            "attempts": attempts,
                        },
                        "normalized_output": {},
                        "raw_text": "",
                        "attempts": attempts,
                        "provider": "openai",
                        "model_id": "gpt-5.4-mini",
                        "model": "openai/gpt-5.4-mini",
                    },
                    metadata={"failure_type": "runtime_timeout"},
                )
                self.pipeline = SimpleNamespace(run=SimpleNamespace(run_id="run-reflection-failure"))
                self.session = SimpleNamespace(session_id="pi-session-failure")

            def as_dict(self, *, full: bool = True) -> dict[str, object]:
                return {"run_id": "run-reflection-failure", "runtime_id": "pi", "status": "failed"}

        class FakeRuntimeService:
            def __init__(self, result: FakeRunResult) -> None:
                self.result = result
                self.run_calls: list[dict[str, object]] = []

            def run(self, **kwargs: object) -> FakeRunResult:
                self.run_calls.append(dict(kwargs))
                return self.result

        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            workspace = project_root / ".ot-workspace"
            project_root.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            fake_runtime = FakeRuntimeService(FakeRunResult())
            service = PiReflectionService(project_root=project_root, workspace_root=workspace, runtime_service=fake_runtime)
            spec = ReflectionJobSpec(
                subject_kind="wallet_style_reflection",
                flow_id="wallet_style_reflection_review",
                system_prompt="system prompt",
                compact_input={"wallet": "0xwallet-runtime-failure"},
                expected_output_schema=build_wallet_style_output_schema(),
                artifact_root=workspace / "reflection-artifacts",
                prompt="return structured reflection",
                injected_context={},
                metadata={},
            )

            result = service.run(spec)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_type, "runtime_timeout")
            self.assertEqual(result.provider, "openai")
            self.assertEqual(result.model_id, "gpt-5.4-mini")
            self.assertFalse(result.raw_text_salvaged)
            self.assertEqual(result.metadata["failure_type"], "runtime_timeout")
            self.assertEqual(result.metadata["provider"], "openai")
            self.assertEqual(result.metadata["model_id"], "gpt-5.4-mini")
            self.assertIn("attempts", result.metadata)
            self.assertTrue(Path(result.artifacts["failure"]).is_file())
            failure_payload = json.loads(Path(result.artifacts["failure"]).read_text(encoding="utf-8"))
            self.assertEqual(failure_payload["failure_type"], "runtime_timeout")
            self.assertEqual(failure_payload["provider"], "openai")
            self.assertEqual(failure_payload["model_id"], "gpt-5.4-mini")
            self.assertFalse(failure_payload["raw_text_salvaged"])


if __name__ == "__main__":
    unittest.main()
