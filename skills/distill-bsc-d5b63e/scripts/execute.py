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


EXECUTION_INTENT = json.loads('{"adapter": "bsc-dex-router", "mode": "burst-scalp", "preferred_workflow": "WBNB -> microcap token -> WBNB rapid cycle with same-minute multi-leg entry", "preflight_checks": ["WBNB balance > $50", "Slippage < 3% for target clip size", "Gas estimate < 0.01 BNB", "Same-minute burst throttle not exceeded"], "route_preferences": ["WBNB direct pairs", "pancakeswap-v3", "high-liquidity microcap pools"], "split_legs": true, "leg_count": 3, "max_position_pct": 0.15, "requires_explicit_approval": false, "metadata": {"chain": "bsc", "entry_factors": [], "risk_filters": [], "market_context": {"macro": {"btc_24h_pct": null, "eth_24h_pct": null, "regime": "unknown"}, "focus_token_context": []}, "review_backend": "pi-reflection-agent:kimi-coding/kimi-k2-thinking", "reflection_flow_id": "wallet_style_reflection_review", "reflection_run_id": "run-cf907193ef55", "reflection_session_id": "pi-session-f19485506a", "reflection_status": "succeeded", "fallback_used": false, "backtest_confidence_label": "high"}}')


def main() -> int:
    context = _load_context()
    project_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(project_root / 'src'))
    from ot_skill_enterprise.execution import prepare_only_result, run_dry_run, run_live
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
        'summary': "Ultra-active BSC scalper trading microcap tokens in sub-15-minute bursts using WBNB. Deploys tiny clip sizes (~0.05% of NAV) across pyramid-style entries, tolerates extreme drawdowns, and maintains zero stablecoin buffer.",
        'execution_readiness': result.get('execution_readiness'),
        'execution_intent': execution_intent,
        'trade_plan': trade_plan,
        'prepared_execution': result.get('prepared_execution'),
        'checks': result.get('checks'),
        'execution_result': result.get('execution'),
        'artifacts': [],
        'metadata': {'skill_family': 'wallet_style', **dict(result.get('metadata') or {})},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
