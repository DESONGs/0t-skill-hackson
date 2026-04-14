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


EXECUTION_INTENT = json.loads('{"adapter": "onchainos_cli", "mode": "dry_run_ready", "preferred_workflow": "swap_execute", "preflight_checks": ["security_token_scan"], "route_preferences": ["WBNB"], "split_legs": true, "leg_count": 4, "max_position_pct": 12.0, "requires_explicit_approval": true, "metadata": {"chain": "bsc", "source": "fallback", "default_source_token": "WBNB", "default_source_token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "default_source_unit_price_usd": 600.0, "entry_factors": [{"factor_type": "volume_spike", "description": "Entry when volume-to-liquidity ratio indicated strong participation.", "frequency": 0.40506329, "avg_pnl_when_present": 231.88011795, "confidence": 0.4835443, "metadata": {"match_count": 32, "profitable_trade_count": 79}}, {"factor_type": "volatility_play", "description": "Entry when volatility regime was elevated.", "frequency": 0.37974684, "avg_pnl_when_present": 243.15216242, "confidence": 0.46582278, "metadata": {"match_count": 30, "profitable_trade_count": 79}}], "risk_filters": [{"filter_type": "transfer_restriction", "description": "PP can restrict transfers or freeze holders", "threshold": true, "is_hard_block": true, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "PP", "metadata": {"risk_names": ["special_fee_exemption_for_token_manager_2", "reentrancy_in_reward_claim_via_external_blacklist_dependency", "centralized_transfer_mode_freeze", "slippage_and_external_dependency_risk_in_fee_swaps"]}}, {"filter_type": "owner_transfer_control", "description": "PP has owner-controlled transfer rules", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "PP", "metadata": {"risk_names": ["special_fee_exemption_for_token_manager_2", "reentrancy_in_reward_claim_via_external_blacklist_dependency", "centralized_transfer_mode_freeze", "slippage_and_external_dependency_risk_in_fee_swaps"]}}, {"filter_type": "owner_transfer_control", "description": "哔哔大队 has owner-controlled transfer rules", "threshold": true, "is_hard_block": false, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "哔哔大队", "metadata": {"risk_names": ["incomplete_blacklist_mechanism", "missing_max_transaction_amount_limit", "transfer_mode_can_be_permanently_changed_to_normal", "potential_precision_loss_in_fee_calculation"]}}, {"filter_type": "holder_concentration", "description": "哔哔大队 top holders control 93.23% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "哔哔大队", "metadata": {"top_holder_share_pct": 93.23144368320406}}, {"filter_type": "transfer_restriction", "description": "XMONEY can restrict transfers or freeze holders", "threshold": true, "is_hard_block": true, "source": "inspect_token.risk_snapshot.metadata.ai_report_summary", "symbol": "XMONEY", "metadata": {"risk_names": ["owner_centralized_transfer_control"]}}, {"filter_type": "holder_concentration", "description": "XMONEY top holders control 60.0% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "XMONEY", "metadata": {"top_holder_share_pct": 60.000000000754085}}, {"filter_type": "holder_concentration", "description": "PIZZA top holders control 75.77% of supply", "threshold": 50, "is_hard_block": false, "source": "inspect_token.holder_snapshot", "symbol": "PIZZA", "metadata": {"top_holder_share_pct": 75.7658027004268}}], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": 2.50071352, "regime": "neutral"}, "focus_token_context": [{"symbol": "PIZZA", "price_change_1h_pct": null, "price_change_24h_pct": null, "momentum_label": null, "volatility_regime": "unknown", "volume_to_liquidity_ratio": null, "liquidity_usd": null}]}, "review_backend": "pi-reflection-agent:kimi-coding/kimi-k2-thinking", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-42d52fc10115", "reflection_session_id": "pi-session-85a327b6e0", "reflection_status": "succeeded", "fallback_used": false, "context_sources": [{"kind": "job_request", "identifier": "0xbac453b9b7f53b35ac906b641925b2f5f2567a89:bsc", "metadata": {"wallet": "0xbac453b9b7f53b35ac906b641925b2f5f2567a89", "chain": "bsc", "target_skill_name": "wallet-style-test-bsc-bac453-20260415"}}, {"kind": "hard_constraint", "value": "Treat injected context as background only."}, {"kind": "hard_constraint", "value": "Return strict JSON only."}, {"kind": "hard_constraint", "value": "Use wallet exactly 0xbac453b9b7f53b35ac906b641925b2f5f2567a89."}, {"kind": "hard_constraint", "value": "Use chain exactly bsc."}, {"kind": "hard_constraint", "value": "Do not emit generic placeholders such as balanced/default/generic/neutral."}, {"kind": "hard_constraint", "value": "profile must include concrete dominant_actions, preferred_tokens, and execution_rules."}, {"kind": "stage_artifact", "identifier": "style-job-5781fa7f86:distill_features", "path": "/public-copy/.ot-workspace/redacted"}, {"kind": "stage_artifact", "identifier": "style-job-5781fa7f86:reflection_report", "path": "/public-copy/.ot-workspace/redacted"}], "backtest_confidence_label": "high", "strategy_quality": "high", "live_cap_usd": 10.0}}')


def main() -> int:
    context = _load_context()
    project_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(project_root / 'src'))
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
        'summary': "High-velocity BSC day-trader deploying same-minute bursts into microcaps like XMONEY and PP, pyramid-averaging into positions with zero stablecoin cushion and holding through -85% drawdowns.",
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
