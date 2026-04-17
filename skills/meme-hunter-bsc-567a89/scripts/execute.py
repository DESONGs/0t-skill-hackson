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


EXECUTION_INTENT = json.loads('{"adapter": "onchainos_cli", "mode": "dry_run_ready", "preferred_workflow": "swap_execute", "preflight_checks": ["security_token_scan"], "route_preferences": ["WBNB"], "split_legs": true, "leg_count": 4, "max_position_pct": 12.0, "requires_explicit_approval": true, "metadata": {"chain": "bsc", "source": "fallback", "default_source_token": "WBNB", "default_source_token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "default_source_unit_price_usd": 600.0, "entry_factors": [{"factor_type": "volume_spike", "description": "Entry when volume-to-liquidity ratio indicated strong participation.", "frequency": 0.41975309, "avg_pnl_when_present": 220.65591941, "confidence": 0.49382716, "metadata": {"match_count": 34, "profitable_trade_count": 81}}, {"factor_type": "volatility_play", "description": "Entry when volatility regime was elevated.", "frequency": 0.41975309, "avg_pnl_when_present": 220.65591941, "confidence": 0.49382716, "metadata": {"match_count": 34, "profitable_trade_count": 81}}, {"factor_type": "dip_buy", "description": "Entry when price retraced sharply over the last hour.", "frequency": 0.02469136, "avg_pnl_when_present": 41.06874275, "confidence": 0.21728395, "metadata": {"match_count": 2, "profitable_trade_count": 81}}], "risk_filters": [{"filter_type": "holder_concentration", "description": "PIZZA top holders control 95.47% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "PIZZA", "metadata": {"top_holder_share_pct": 95.46979761849074}}, {"filter_type": "owner_transfer_control", "description": "哔哔大队 has owner-controlled transfer rules", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "哔哔大队", "metadata": {"risk_names": ["incomplete_blacklist_mechanism", "missing_max_transaction_amount_limit", "transfer_mode_can_be_permanently_changed_to_normal", "potential_precision_loss_in_fee_calculation"]}}, {"filter_type": "holder_concentration", "description": "哔哔大队 top holders control 93.23% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "哔哔大队", "metadata": {"top_holder_share_pct": 93.23144368320406}}, {"filter_type": "transfer_restriction", "description": "PP can restrict transfers or freeze holders", "threshold": true, "is_hard_block": true, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "PP", "metadata": {"risk_names": ["special_fee_exemption_for_token_manager_2", "reentrancy_in_reward_claim_via_external_blacklist_dependency", "centralized_transfer_mode_freeze", "slippage_and_external_dependency_risk_in_fee_swaps"]}}, {"filter_type": "owner_transfer_control", "description": "PP has owner-controlled transfer rules", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "PP", "metadata": {"risk_names": ["special_fee_exemption_for_token_manager_2", "reentrancy_in_reward_claim_via_external_blacklist_dependency", "centralized_transfer_mode_freeze", "slippage_and_external_dependency_risk_in_fee_swaps"]}}, {"filter_type": "transfer_restriction", "description": "CX can restrict transfers or freeze holders", "threshold": true, "is_hard_block": true, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "CX", "metadata": {"risk_names": ["transfer_restriction_mode", "transfer_controlled_mode", "owner-only_initialization_and_mode_setting"]}}, {"filter_type": "owner_transfer_control", "description": "CX has owner-controlled transfer rules", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "CX", "metadata": {"risk_names": ["transfer_restriction_mode", "transfer_controlled_mode", "owner-only_initialization_and_mode_setting"]}}, {"filter_type": "mint_burn_risk", "description": "CX has owner-controlled supply or burn risk", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "CX", "metadata": {"risk_names": ["transfer_restriction_mode", "transfer_controlled_mode", "owner-only_initialization_and_mode_setting"]}}, {"filter_type": "holder_concentration", "description": "CX top holders control 26.05% of supply", "threshold": 20, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "CX", "metadata": {"top_holder_share_pct": 26.051968327047963}}], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": -3.10293036, "regime": "neutral"}, "focus_token_context": [{"symbol": "CX", "price_change_1h_pct": null, "price_change_24h_pct": null, "momentum_label": null, "volatility_regime": "extreme", "volume_to_liquidity_ratio": null, "liquidity_usd": null}]}, "primary_archetype": "meme_hunter", "secondary_archetypes": ["degen_sniper", "scalper"], "behavioral_patterns": ["small_cap_bias", "profit_recycling", "fast_rotation", "conviction_holding"], "archetype_confidence": 0.95, "archetype_evidence_summary": ["trades_per_day=27.00", "avg_holding_seconds=15301", "open_position_ratio=0.10", "small_cap_trade_ratio=1.00", "profit_add_ratio=0.27"], "archetype_token_preference": ["PP", "10亿", "Crypto Summer"], "review_backend": "wallet-style-extractor-fallback", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-e0fe9b20a6a2", "reflection_session_id": "pi-session-eb9ef53b40", "reflection_status": "failed", "fallback_used": true, "context_sources": [{"kind": "job_request", "identifier": "0xbac453b9b7f53b35ac906b641925b2f5f2567a89:bsc", "metadata": {"wallet": "0xbac453b9b7f53b35ac906b641925b2f5f2567a89", "chain": "bsc", "target_skill_name": "wallet-style-567a89"}}, {"kind": "retry_reason"}, {"kind": "hard_constraint", "value": "Treat injected context as background only."}, {"kind": "hard_constraint", "value": "Return strict JSON only."}, {"kind": "hard_constraint", "value": "Use wallet exactly 0xbac453b9b7f53b35ac906b641925b2f5f2567a89."}, {"kind": "hard_constraint", "value": "Use chain exactly bsc."}, {"kind": "hard_constraint", "value": "Produce only the minimal distill contract; Python will assemble the final profile, strategy, and execution intent."}, {"kind": "hard_constraint", "value": "Use derived_stats.primary_archetype, secondary_archetypes, behavioral_patterns, archetype_confidence, and archetype_evidence_summary as the primary taxonomy when present."}, {"kind": "hard_constraint", "value": "Legal review_status values are generate, generate_with_low_confidence, insufficient_signal, no_pattern_detected, needs_manual_review, and runtime_failed."}, {"kind": "hard_constraint", "value": "insufficient_signal, no_pattern_detected, and needs_manual_review are successful outcomes and should not fabricate a strong setup."}, {"kind": "hard_constraint", "value": "If you are unsure, keep optional fields empty and still return valid wallet-specific JSON."}, {"kind": "hard_constraint", "value": "The previous attempt failed. Keep the response shorter and preserve only wallet-specific evidence."}, {"kind": "stage_artifact", "identifier": "style-job-06e02dce03:distill_features", "path": "/redacted/.ot-workspace/style-distillations/style-job-06e02dce03/context/stage_distill_features.json"}, {"kind": "stage_artifact", "identifier": "style-job-06e02dce03:reflection_report", "path": "/redacted/.ot-workspace/style-distillations/style-job-06e02dce03/context/stage_reflection.json"}], "backtest_confidence_label": "medium", "strategy_quality": "medium", "live_cap_usd": 10.0}}')


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
        'summary': "0xbac453b9b7f53b35ac906b641925b2f5f2567a89 on bsc maps to meme_hunter, trades with high-frequency rotation, leans conservative, shows distributed basket, and most often acts through sell around PP.",
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
