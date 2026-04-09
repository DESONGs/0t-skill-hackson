from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, field_validator

from .common import ContractModel, EnvelopeMeta, ServiceEnvelope, ServiceError, utc_now


def _strip_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


class TokenReference(ContractModel):
    identifier: str = Field(min_length=1)
    chain: Optional[str] = None
    token_address: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    rank: Optional[int] = Field(default=None, ge=1)
    score: Optional[float] = Field(default=None, ge=0)
    source: Optional[str] = None

    @field_validator("identifier", "chain", "token_address", "symbol", "name", "source", mode="before")
    @classmethod
    def _normalize_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class RankingContext(ContractModel):
    title: Optional[str] = None
    window: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


class SourceMeta(ContractModel):
    provider: str = "ave"
    request_id: Optional[str] = None
    fetched_at: datetime = Field(default_factory=utc_now)
    cached: bool = False
    source_version: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)


class PairReference(ContractModel):
    identifier: str = Field(min_length=1)
    chain: Optional[str] = None
    pair_address: Optional[str] = None
    dex: Optional[str] = None
    base_token_ref: Optional[TokenReference] = None
    quote_token_ref: Optional[TokenReference] = None

    @field_validator("identifier", "chain", "pair_address", "dex", mode="before")
    @classmethod
    def _normalize_pair_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class MarketSnapshot(ContractModel):
    price_usd: Optional[float] = Field(default=None, ge=0)
    market_cap_usd: Optional[float] = Field(default=None, ge=0)
    fdv_usd: Optional[float] = Field(default=None, ge=0)
    liquidity_usd: Optional[float] = Field(default=None, ge=0)
    volume_24h_usd: Optional[float] = Field(default=None, ge=0)
    status: Literal["available", "unavailable"] = "available"
    note: Optional[str] = None


class RiskSnapshot(ContractModel):
    risk_level: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    honeypot: Optional[bool] = None
    buy_tax_bps: Optional[int] = Field(default=None, ge=0)
    sell_tax_bps: Optional[int] = Field(default=None, ge=0)
    status: Literal["available", "unavailable"] = "available"
    note: Optional[str] = None


class HolderEntry(ContractModel):
    holder_address: str = Field(min_length=1)
    quantity: Optional[float] = Field(default=None, ge=0)
    value_usd: Optional[float] = Field(default=None, ge=0)
    share_pct: Optional[float] = Field(default=None, ge=0, le=100)
    label: Optional[str] = None

    @field_validator("holder_address", "label", mode="before")
    @classmethod
    def _normalize_holder_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class HolderSnapshot(ContractModel):
    holder_count: Optional[int] = Field(default=None, ge=0)
    top_holder_share_pct: Optional[float] = Field(default=None, ge=0, le=100)
    holders: list[HolderEntry] = Field(default_factory=list)
    status: Literal["available", "unavailable"] = "available"
    note: Optional[str] = None


class OhlcvPoint(ContractModel):
    timestamp: datetime
    open: Optional[float] = Field(default=None, ge=0)
    high: Optional[float] = Field(default=None, ge=0)
    low: Optional[float] = Field(default=None, ge=0)
    close: Optional[float] = Field(default=None, ge=0)
    volume: Optional[float] = Field(default=None, ge=0)


class RecentSwap(ContractModel):
    tx_hash: str = Field(min_length=1)
    timestamp: datetime
    side: Literal["buy", "sell", "unknown"] = "unknown"
    token_ref: Optional[TokenReference] = None
    amount_base: Optional[float] = None
    amount_quote: Optional[float] = None
    trader: Optional[str] = None


class FlowSummary(ContractModel):
    buy_count: Optional[int] = Field(default=None, ge=0)
    sell_count: Optional[int] = Field(default=None, ge=0)
    net_flow_usd: Optional[float] = None
    large_trade_count: Optional[int] = Field(default=None, ge=0)
    note: Optional[str] = None


class WalletSummary(ContractModel):
    wallet_address: str = Field(min_length=1)
    chain: Optional[str] = None
    label: Optional[str] = None
    balance_usd: Optional[float] = Field(default=None, ge=0)
    token_count: Optional[int] = Field(default=None, ge=0)
    status: Literal["available", "unavailable"] = "available"
    note: Optional[str] = None

    @field_validator("wallet_address", "chain", "label", mode="before")
    @classmethod
    def _normalize_wallet_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class HoldingItem(ContractModel):
    token_ref: TokenReference
    quantity: Optional[float] = Field(default=None, ge=0)
    value_usd: Optional[float] = Field(default=None, ge=0)
    allocation_pct: Optional[float] = Field(default=None, ge=0, le=100)


class WalletActivityItem(ContractModel):
    tx_hash: str = Field(min_length=1)
    timestamp: datetime
    action: Literal["buy", "sell", "transfer", "swap", "unknown"] = "unknown"
    token_ref: Optional[TokenReference] = None
    amount_usd: Optional[float] = None
    note: Optional[str] = None


class SignalItem(ContractModel):
    signal_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    chain: Optional[str] = None
    token_ref: Optional[TokenReference] = None
    description: Optional[str] = None
    occurred_at: Optional[datetime] = None
    source: Optional[str] = None


class TokenDiscoveryDomain(ContractModel):
    token_refs: list[TokenReference] = Field(default_factory=list)
    ranking_context: Optional[RankingContext] = None
    source_meta: SourceMeta = Field(default_factory=SourceMeta)


class TokenProfileDomain(ContractModel):
    identity: TokenReference
    market_snapshot: MarketSnapshot
    risk_snapshot: RiskSnapshot
    holder_snapshot: HolderSnapshot
    main_pair_ref: Optional[PairReference] = None
    source_meta: SourceMeta = Field(default_factory=SourceMeta)


class MarketActivityDomain(ContractModel):
    selected_pair: Optional[PairReference] = None
    ohlcv: list[OhlcvPoint] = Field(default_factory=list)
    recent_swaps: list[RecentSwap] = Field(default_factory=list)
    flow_summary: Optional[FlowSummary] = None
    source_meta: SourceMeta = Field(default_factory=SourceMeta)


class WalletProfileDomain(ContractModel):
    wallet_summary: WalletSummary
    holdings: list[HoldingItem] = Field(default_factory=list)
    recent_activity: list[WalletActivityItem] = Field(default_factory=list)
    source_meta: SourceMeta = Field(default_factory=SourceMeta)


class SignalFeedDomain(ContractModel):
    signals: list[SignalItem] = Field(default_factory=list)
    linked_token_refs: list[TokenReference] = Field(default_factory=list)
    source_meta: SourceMeta = Field(default_factory=SourceMeta)


class DiscoverTokensRequest(ContractModel):
    query: Optional[str] = None
    chain: Optional[str] = None
    source: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("query", "chain", "source", mode="before")
    @classmethod
    def _normalize_request_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class InspectTokenRequest(ContractModel):
    token_ref: TokenReference
    include_holders: bool = True
    include_risk: bool = True


class InspectMarketRequest(ContractModel):
    token_ref: TokenReference
    pair_ref: Optional[PairReference] = None
    interval: str = "1h"
    window: str = "24h"

    @field_validator("interval", "window", mode="before")
    @classmethod
    def _normalize_window_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class InspectWalletRequest(ContractModel):
    wallet: str = Field(min_length=1)
    chain: Optional[str] = None
    include_holdings: bool = True
    include_activity: bool = True

    @field_validator("wallet", "chain", mode="before")
    @classmethod
    def _normalize_wallet_request_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


class ReviewSignalsRequest(ContractModel):
    chain: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)
    token_ref: Optional[TokenReference] = None

    @field_validator("chain", mode="before")
    @classmethod
    def _normalize_signal_text(cls, value: Optional[str]) -> Optional[str]:
        return _strip_text(value)


DiscoverTokensResponse = ServiceEnvelope[TokenDiscoveryDomain]
InspectTokenResponse = ServiceEnvelope[TokenProfileDomain]
InspectMarketResponse = ServiceEnvelope[MarketActivityDomain]
InspectWalletResponse = ServiceEnvelope[WalletProfileDomain]
ReviewSignalsResponse = ServiceEnvelope[SignalFeedDomain]
