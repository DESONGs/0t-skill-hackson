from __future__ import annotations

import json
from pathlib import Path
import sys


def _load_context() -> dict:
    if len(sys.argv) > 1:
        candidate = sys.argv[1]
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return json.loads(candidate)
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)
    return {}


EXECUTION_INTENT = json.loads('{"adapter": "onchainos_cli", "mode": "dry_run_ready", "preferred_workflow": "swap_execute", "preflight_checks": ["security_token_scan"], "route_preferences": ["USDT"], "split_legs": true, "leg_count": 4, "max_position_pct": 12.0, "requires_explicit_approval": true, "metadata": {"chain": "bsc", "source": "fallback", "default_source_token": "WBNB", "default_source_token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "default_source_unit_price_usd": 600.0, "entry_factors": [{"factor_type": "momentum_chase", "description": "Fallback inferred from profitable short-hold trades when direct market context is unavailable.", "frequency": 1.0, "avg_pnl_when_present": 8.04062468, "confidence": 0.55, "metadata": {"source_mode": "completed_trade_pattern", "match_count": 1}}, {"factor_type": "volume_spike", "description": "Fallback inferred from profitable split-leg participation when direct liquidity context is unavailable.", "frequency": 1.0, "avg_pnl_when_present": 8.04062468, "confidence": 0.5, "metadata": {"source_mode": "completed_trade_pattern", "match_count": 1}}], "risk_filters": [{"filter_type": "holder_concentration", "description": "DiamondBalls top holders control 100.0% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "DiamondBalls", "metadata": {"top_holder_share_pct": 100.0}}, {"filter_type": "holder_concentration", "description": "坎杜拉 top holders control 100.0% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "坎杜拉", "metadata": {"top_holder_share_pct": 100.0}}, {"filter_type": "holder_concentration", "description": "ASTER top holders control 39.14% of supply", "threshold": 20, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "ASTER", "metadata": {"top_holder_share_pct": 39.143003510875126}}, {"filter_type": "holder_concentration", "description": "GENIUS top holders control 56.0% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "GENIUS", "metadata": {"top_holder_share_pct": 56.00000000000001}}], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": -2.3121694, "regime": "neutral"}, "focus_token_context": [{"symbol": "GENIUS", "price_change_1h_pct": null, "price_change_24h_pct": null, "momentum_label": null, "volatility_regime": "high", "volume_to_liquidity_ratio": null, "liquidity_usd": null}]}, "primary_archetype": "meme_hunter", "secondary_archetypes": ["asymmetric_bettor", "degen_sniper"], "behavioral_patterns": ["small_cap_bias", "profit_recycling", "fast_rotation", "conviction_holding"], "archetype_confidence": 0.8186, "archetype_evidence_summary": ["trades_per_day=3.44", "avg_holding_seconds=287", "open_position_ratio=0.50", "small_cap_trade_ratio=1.00", "profit_add_ratio=0.00"], "archetype_token_preference": ["GENIUS", "the", "EGGIFY"], "review_backend": "wallet-style-extractor-fallback", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-7c50d919995c", "reflection_session_id": "pi-session-1aa915ec08", "reflection_status": "failed", "fallback_used": true, "context_sources": [{"kind": "job_request", "identifier": "0x9998c32dc444709f7b613aa05666325edbc0bc2d:bsc", "metadata": {"wallet": "0x9998c32dc444709f7b613aa05666325edbc0bc2d", "chain": "bsc", "target_skill_name": "wallet-style-c0bc2d"}}, {"kind": "retry_reason"}, {"kind": "hard_constraint", "value": "Treat injected context as background only."}, {"kind": "hard_constraint", "value": "Return strict JSON only."}, {"kind": "hard_constraint", "value": "Use wallet exactly 0x9998c32dc444709f7b613aa05666325edbc0bc2d."}, {"kind": "hard_constraint", "value": "Use chain exactly bsc."}, {"kind": "hard_constraint", "value": "Do not emit generic placeholders such as balanced/default/generic/neutral."}, {"kind": "hard_constraint", "value": "Use derived_stats.primary_archetype, secondary_archetypes, behavioral_patterns, archetype_confidence, and archetype_evidence_summary as the primary taxonomy when they are present."}, {"kind": "hard_constraint", "value": "Legal review.status values are generate, generate_with_low_confidence, insufficient_signal, no_pattern_detected, needs_manual_review, and runtime_failed."}, {"kind": "hard_constraint", "value": "insufficient_signal, no_pattern_detected, and needs_manual_review are successful review states and must set review.should_generate_candidate=false."}, {"kind": "hard_constraint", "value": "When signal is low, keep the response evidence-first and non-generic; strategy.entry_conditions may be minimal but must stay explicit about data_source."}, {"kind": "hard_constraint", "value": "For generating states, profile must include concrete dominant_actions, preferred_tokens, and execution_rules."}, {"kind": "hard_constraint", "value": "The previous reflection output was rejected for being too generic or inconsistent. Correct the listed issues directly."}, {"kind": "stage_artifact", "identifier": "style-job-5e3c4b5e91:distill_features", "path": "/redacted/.ot-workspace/style-distillations/style-job-5e3c4b5e91/context/stage_distill_features.json"}, {"kind": "stage_artifact", "identifier": "style-job-5e3c4b5e91:reflection_report", "path": "/redacted/.ot-workspace/style-distillations/style-job-5e3c4b5e91/context/stage_reflection.json"}], "backtest_confidence_label": "low", "strategy_quality": "low", "live_cap_usd": 10.0}}')


def main() -> int:
    context = _load_context()
    project_root = Path(__file__).resolve().parents[3]
    source_roots = [
        project_root / 'src',
        Path("/redacted/src"),
    ]
    for source_root in source_roots:
        if source_root.is_dir():
            resolved = str(source_root.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
    from ot_skill_enterprise.env_bootstrap import load_local_env
    from ot_skill_enterprise.execution import prepare_only_result, run_dry_run, run_live
    load_local_env()
    trade_plan = dict(context.get('trade_plan') or {})
    execution_intent = dict(context.get('execution_intent') or EXECUTION_INTENT)
    mode = str(context.get('mode') or 'prepare_only').strip() or 'prepare_only'
    approval_granted = bool(context.get('approval_granted'))
    if not trade_plan:
        payload = {
            'ok': False,
            'action': 'execute',
            'summary': 'trade_plan is required',
            'execution_readiness': 'blocked_by_risk',
            'artifacts': [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    if mode == 'prepare_only':
        result = prepare_only_result(trade_plan, execution_intent, project_root=project_root)
    elif mode == 'dry_run':
        result = run_dry_run(trade_plan, execution_intent, project_root=project_root)
    elif mode == 'live':
        live_intent = dict(execution_intent)
        live_intent['requires_explicit_approval'] = not approval_granted
        result = run_live(trade_plan, live_intent, project_root=project_root)
    else:
        result = {
            'ok': False,
            'mode': mode,
            'execution_readiness': 'blocked_by_risk',
            'prepared_execution': {},
            'checks': [],
            'execution': {},
        }
    payload = {
        'ok': bool(result.get('ok')),
        'action': 'execute',
        'summary': "0x9998c32dc444709f7b613aa05666325edbc0bc2d on bsc maps to meme_hunter, trades with high-frequency rotation, leans conservative, shows distributed basket, and most often acts through swap around GENIUS.",
        'execution_readiness': result.get('execution_readiness'),
        'execution_intent': execution_intent,
        'trade_plan': result.get('trade_plan') or trade_plan,
        'prepared_execution': result.get('prepared_execution'),
        'checks': result.get('checks'),
        'execution_result': result.get('execution'),
        'approval_required': result.get('approval_required'),
        'approval_result': result.get('approval_result'),
        'simulation_result': result.get('simulation_result'),
        'broadcast_results': result.get('broadcast_results'),
        'tx_hashes': result.get('tx_hashes'),
        'live_cap_usd': result.get('live_cap_usd'),
        'executed_leg_count': result.get('executed_leg_count'),
        'artifacts': [],
        'metadata': {'skill_family': 'wallet_style', **dict(result.get('metadata') or {})},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
