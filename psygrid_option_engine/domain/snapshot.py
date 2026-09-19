"""Canonical `MarketSnapshot` — the one shape every layer above `data/`
operates on, regardless of the upstream wire format.

This decoupling is deliberate (see docs/ENDPOINTS.md, "Verification
status"): the upstream field-name contract is unverified, so exactly one
module (`data/snapshot_builder.py`) is responsible for turning raw JSON
into these types. Every other module — structure, momentum, authorization,
option selection, execution — is written against these types and does not
change when the upstream adapter is corrected against real samples.

Every externally-sourced value is a `SourcedField[T]`; only values this
engine itself computes (e.g. an aggregated candle, a derived VWAP) are
bare, and those are always documented as derived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.signals.schema import DataQuality

OptionType = Literal["CE", "PE"]


@dataclass(frozen=True)
class OHLC:
    open: SourcedField[float]
    high: SourcedField[float]
    low: SourcedField[float]
    close: SourcedField[float]
    volume: SourcedField[float]


@dataclass(frozen=True)
class UnderlyingSnapshot:
    symbol: str
    ltp: SourcedField[float]
    day_ohlc: OHLC
    prev_day_ohlc: OHLC | None
    prev_week_ohlc: OHLC | None
    candles: dict[Timeframe, tuple[Candle, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class FuturesLeg:
    expiry: date | None
    ltp: SourcedField[float]
    oi: SourcedField[float]
    oi_change: SourcedField[float]
    volume: SourcedField[float]
    ohlc: OHLC | None


@dataclass(frozen=True)
class FuturesSnapshot:
    underlying: str
    legs: tuple[FuturesLeg, ...] = field(default_factory=tuple)

    @property
    def nearest(self) -> FuturesLeg | None:
        return self.legs[0] if self.legs else None


@dataclass(frozen=True)
class OptionLeg:
    security_id: str
    symbol: str
    strike: float
    option_type: OptionType
    expiry: date | None
    ltp: SourcedField[float]
    bid: SourcedField[float]
    ask: SourcedField[float]
    volume: SourcedField[float]
    oi: SourcedField[float]
    oi_change: SourcedField[float]
    iv: SourcedField[float]
    delta: SourcedField[float]
    gamma: SourcedField[float]
    theta: SourcedField[float]
    vega: SourcedField[float]

    @property
    def spread(self) -> float | None:
        if not (self.bid.available and self.ask.available):
            return None
        if self.bid.value is None or self.ask.value is None:
            return None
        return self.ask.value - self.bid.value


@dataclass(frozen=True)
class OptionChainSnapshot:
    underlying: str
    legs: tuple[OptionLeg, ...] = field(default_factory=tuple)

    def for_expiry(self, expiry: date) -> tuple[OptionLeg, ...]:
        return tuple(leg for leg in self.legs if leg.expiry == expiry)

    def expiries(self) -> tuple[date, ...]:
        seen = sorted({leg.expiry for leg in self.legs if leg.expiry is not None})
        return tuple(seen)

    def leg(self, strike: float, option_type: OptionType, expiry: date | None = None) -> OptionLeg | None:
        for candidate in self.legs:
            if candidate.strike == strike and candidate.option_type == option_type:
                if expiry is None or candidate.expiry == expiry:
                    return candidate
        return None


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: float
    orders: int | None = None


@dataclass(frozen=True)
class InstrumentDepth:
    security_id: str
    bids: tuple[DepthLevel, ...] = field(default_factory=tuple)
    asks: tuple[DepthLevel, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DepthSnapshot:
    underlying: str
    by_security_id: dict[str, InstrumentDepth] = field(default_factory=dict)


@dataclass(frozen=True)
class BreadthSnapshot:
    advances: SourcedField[float]
    declines: SourcedField[float]
    unchanged: SourcedField[float]


@dataclass(frozen=True)
class SectorSnapshot:
    name: str
    change_pct: SourcedField[float]


@dataclass(frozen=True)
class VixSnapshot:
    value: SourcedField[float]


@dataclass(frozen=True)
class GlobalContextSeries:
    """A single delayed macro series. `source_date` is the upstream's own
    reported as-of date for the value — per docs/ENDPOINTS.md this must
    never be conflated with a live tick, hence it is kept separate from
    `SourcedField.observed_at` (which is about our fetch freshness, not
    the series' own inherent staleness)."""

    name: str
    value: SourcedField[float]
    source_date: date | None


@dataclass(frozen=True)
class NewsItem:
    headline: str
    published_at: datetime | None
    source: str


@dataclass(frozen=True)
class MarketSnapshot:
    """The canonical, information-boundary-scoped view of one underlying's
    market state, built by `data/snapshot_builder.py` from a `RawFetchBundle`
    that was itself fetched at or before `as_of`."""

    underlying: str
    as_of: datetime
    data_quality: DataQuality
    underlying_snapshot: UnderlyingSnapshot | None
    futures: FuturesSnapshot | None
    options: OptionChainSnapshot | None
    depth: DepthSnapshot | None
    breadth: BreadthSnapshot | None
    sectors: tuple[SectorSnapshot, ...] = field(default_factory=tuple)
    vix: VixSnapshot | None = None
    global_context: tuple[GlobalContextSeries, ...] = field(default_factory=tuple)
    news: tuple[NewsItem, ...] = field(default_factory=tuple)
