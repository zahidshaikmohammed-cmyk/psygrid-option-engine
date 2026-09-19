"""Shared types for the structure engine. Pure data, no logic — see
swings.py/levels.py/liquidity.py/regime.py/engine.py for the functions
that build these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True)
class SwingPoint:
    kind: SwingKind
    index: int
    time: datetime
    price: float
    confirmed_at: datetime


class StructureLabel(StrEnum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


@dataclass(frozen=True)
class LabeledSwing:
    swing: SwingPoint
    label: StructureLabel | None  # None: first swing of its kind, no prior reference


class TrendBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class StructureState:
    swings: tuple[LabeledSwing, ...]
    trend_bias: TrendBias
    bias_evidence: tuple[str, ...]
    invalidation_long: float | None  # break below here invalidates a bullish thesis
    invalidation_short: float | None  # break above here invalidates a bearish thesis


@dataclass(frozen=True)
class SessionLevels:
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    prev_day_close: float | None = None
    today_open: float | None = None
    today_high: float | None = None
    today_low: float | None = None
    session_midpoint: float | None = None
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    prev_week_high: float | None = None
    prev_week_low: float | None = None
    prev_week_close: float | None = None
    current_week_high: float | None = None
    current_week_low: float | None = None


class LiquidityKind(StrEnum):
    PDH = "PDH"
    PDL = "PDL"
    PWH = "PWH"
    PWL = "PWL"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    OPENING_RANGE_HIGH = "OPENING_RANGE_HIGH"
    OPENING_RANGE_LOW = "OPENING_RANGE_LOW"
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    OI_WALL_CE = "OI_WALL_CE"
    OI_WALL_PE = "OI_WALL_PE"


@dataclass(frozen=True)
class LiquidityZone:
    kind: LiquidityKind
    level: float
    note: str


class ReactionKind(StrEnum):
    UNTESTED = "UNTESTED"
    ACCEPTANCE = "ACCEPTANCE"
    REJECTION = "REJECTION"
    FAILED_BREAK = "FAILED_BREAK"
    SWEEP = "SWEEP"
    CONTINUATION = "CONTINUATION"


@dataclass(frozen=True)
class LevelReaction:
    zone: LiquidityZone
    reaction: ReactionKind


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    EXPANSION = "EXPANSION"
    COMPRESSION = "COMPRESSION"
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    PULLBACK = "PULLBACK"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    REVERSAL_ATTEMPT = "REVERSAL_ATTEMPT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class RegimeAssessment:
    regime: MarketRegime
    evidence: tuple[str, ...] = field(default_factory=tuple)
    independent_streams: int = 0


@dataclass(frozen=True)
class StructureAnalysis:
    """The full output of `structure/engine.py::analyze_structure`."""

    structure: StructureState
    levels: SessionLevels
    liquidity_zones: tuple[LiquidityZone, ...]
    level_reactions: tuple[LevelReaction, ...]
    regime: RegimeAssessment
    vwap: float | None
    vwap_relation: str | None  # "ABOVE" / "BELOW" / "AT" / None if unavailable
