from __future__ import annotations

from typing import Any


STABLECOIN_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}

_CHAIN_ALIASES = {
    "eth": "ethereum",
    "arb": "arbitrum",
    "matic": "polygon",
    "pol": "polygon",
    "avax": "avalanche",
    "ftm": "fantom",
}

_CHAIN_BENCHMARK_ASSETS = {
    "ethereum": {
        "default_source_token": "WETH",
        "default_source_token_address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "default_target_token": "WETH",
        "default_target_token_address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "quote_symbols": ("ETH", "WETH"),
    },
    "base": {
        "default_source_token": "WETH",
        "default_source_token_address": "0x4200000000000000000000000000000000000006",
        "default_target_token": "WETH",
        "default_target_token_address": "0x4200000000000000000000000000000000000006",
        "quote_symbols": ("ETH", "WETH"),
    },
    "bsc": {
        "default_source_token": "WBNB",
        "default_source_token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "default_source_unit_price_usd": 600.0,
        "default_target_token": "WBNB",
        "default_target_token_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "quote_symbols": ("BNB", "WBNB"),
    },
    "arbitrum": {
        "default_source_token": "WETH",
        "default_source_token_address": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "default_target_token": "WETH",
        "default_target_token_address": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "quote_symbols": ("ETH", "WETH"),
    },
    "polygon": {
        "default_source_token": "WPOL",
        "default_source_token_address": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
        "default_target_token": "WPOL",
        "default_target_token_address": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
        "quote_symbols": ("POL", "MATIC", "WPOL", "WMATIC"),
    },
    "optimism": {
        "default_source_token": "WETH",
        "default_source_token_address": "0x4200000000000000000000000000000000000006",
        "default_target_token": "WETH",
        "default_target_token_address": "0x4200000000000000000000000000000000000006",
        "quote_symbols": ("ETH", "WETH"),
    },
    "avalanche": {
        "default_source_token": "WAVAX",
        "default_source_token_address": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",
        "default_target_token": "WAVAX",
        "default_target_token_address": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",
        "quote_symbols": ("AVAX", "WAVAX"),
    },
    "fantom": {
        "default_source_token": "WFTM",
        "default_source_token_address": "0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83",
        "default_target_token": "WFTM",
        "default_target_token_address": "0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83",
        "quote_symbols": ("FTM", "WFTM"),
    },
    "linea": {
        "default_source_token": "WETH",
        "default_source_token_address": "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f",
        "default_target_token": "WETH",
        "default_target_token_address": "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f",
        "quote_symbols": ("ETH", "WETH"),
    },
    "zksync": {
        "default_source_token": "WETH",
        "default_source_token_address": "0x5aea5775959fbc2557cc8789bc1bf90a239d9a91",
        "default_target_token": "WETH",
        "default_target_token_address": "0x5aea5775959fbc2557cc8789bc1bf90a239d9a91",
        "quote_symbols": ("ETH", "WETH"),
    },
}


def normalize_chain_name(chain: Any) -> str:
    text = str(chain or "").strip().lower()
    return _CHAIN_ALIASES.get(text, text)


def chain_benchmark_defaults(chain: Any) -> dict[str, Any]:
    normalized = normalize_chain_name(chain)
    asset = dict(_CHAIN_BENCHMARK_ASSETS.get(normalized) or {})
    asset.pop("quote_symbols", None)
    return asset


def chain_quote_symbols(chain: Any | None = None) -> set[str]:
    if chain is None:
        values: set[str] = set()
        for asset in _CHAIN_BENCHMARK_ASSETS.values():
            values.update(str(symbol).upper() for symbol in asset.get("quote_symbols") or ())
        return values
    normalized = normalize_chain_name(chain)
    asset = dict(_CHAIN_BENCHMARK_ASSETS.get(normalized) or {})
    return {str(symbol).upper() for symbol in asset.get("quote_symbols") or ()}


def chain_wrapped_native(chain: Any) -> tuple[str, str, float | None] | None:
    defaults = chain_benchmark_defaults(chain)
    symbol = str(defaults.get("default_source_token") or "").strip()
    address = str(defaults.get("default_source_token_address") or "").strip()
    if not symbol or not address:
        return None
    price_hint = defaults.get("default_source_unit_price_usd")
    if price_hint in (None, ""):
        return symbol, address, None
    return symbol, address, float(price_hint)


CHAIN_BENCHMARK_DEFAULTS = {
    chain: chain_benchmark_defaults(chain)
    for chain in _CHAIN_BENCHMARK_ASSETS
}
