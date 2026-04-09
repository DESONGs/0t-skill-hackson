from __future__ import annotations

import sys
import textwrap
from pathlib import Path

SERVICE_DIR = Path("/Users/chenge/Desktop/0t-ave/0t-skill_enterprise/services/ave-data-service")
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from models import InspectWalletRequest  # noqa: E402
from providers import AveRestProvider  # noqa: E402


def test_real_provider_wrapper_executes_subprocess_and_normalizes_wallet_profile(tmp_path) -> None:
    script_path = tmp_path / "ave_data_rest.py"
    script_path.write_text(
        textwrap.dedent(
            """
import json
import sys

command = sys.argv[1]
args = sys.argv[2:]

def emit(payload):
    print(json.dumps(payload))

if command == "wallet-info":
    wallet = args[args.index("--wallet") + 1]
    chain = args[args.index("--chain") + 1]
    emit({
        "request_id": "wallet-real",
        "data": {
            "wallet_address": wallet,
            "chain": chain,
            "label": "stub wallet",
            "balance_usd": 1234.5,
            "token_count": 2,
            "source_meta": {"request_id": "wallet-real", "source_version": "stub"}
        }
    })
elif command == "wallet-tokens":
    emit({
        "data": {
            "tokens": [
                {
                    "token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
                    "quantity": 10,
                    "value_usd": 100.5,
                    "allocation_pct": 55.0
                },
                {
                    "token_ref": {"identifier": "eth:beta", "chain": "eth", "symbol": "BETA"},
                    "quantity": 5,
                    "value_usd": 60.0,
                    "allocation_pct": 30.0
                }
            ]
        }
    })
elif command == "address-txs":
    emit({
        "data": {
            "items": [
                {
                    "tx_hash": "0x1",
                    "timestamp": "2026-04-09T00:10:00Z",
                    "action": "swap",
                    "token_ref": {"identifier": "eth:alpha", "chain": "eth", "symbol": "ALPHA"},
                    "amount_usd": 42.0
                }
            ]
        }
    })
else:
    emit({"data": {}})
"""
        )
    )

    provider = AveRestProvider(script_path=script_path, python_executable=sys.executable, timeout_seconds=5.0)
    response = provider.inspect_wallet(InspectWalletRequest(wallet="0xwallet", chain="eth"))

    assert response["wallet_summary"]["wallet_address"] == "0xwallet"
    assert response["wallet_summary"]["balance_usd"] == 1234.5
    assert len(response["holdings"]) == 2
    assert response["holdings"][0]["token_ref"]["identifier"] == "eth:alpha"
    assert len(response["recent_activity"]) == 1
    assert response["source_meta"]["request_id"] == "wallet-real"
