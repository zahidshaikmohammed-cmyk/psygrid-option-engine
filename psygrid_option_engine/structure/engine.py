"""Orchestrator: `MarketSnapshot` -> `StructureAnalysis`.

Wires together swings.py/levels.py/liquidity.py/regime.py so callers
(authorization/, run_engine.py) have one entry point. Contains no
decision logic itself — it only assembles observational evidence.
"""

from __future__ import annotations

from psygrid_option_engine.config.session import IST, SessionWindow
from psygrid_option_engine.domain.snapshot import MarketSnapshot
from psygrid_option_engine.domain.timeframe import Timeframe
from psygrid_option_engine.market.indicators import adx as adx_series
from psygrid_option_engine.market.indicators import bollinger_bands
from psygrid_option_engine.market.indicators import vwap as vwap_series
from psygrid_option_engine.structure.levels import compute_session_levels
from psygrid_option_engine.structure.liquidity import classify_reactions_at_zones, identify_liquidity_zones
from psygrid_option_engine.structure.regime import classify_regime
from psygrid_option_engine.structure.swings import build_structure_state, find_swings
from psygrid_option_engine.structure.types import (
    LevelReaction,
    ReactionKind,
    SessionLevels,
    StructureAnalysis,
)

_DEFAULT_SWING_LOOKBACK = 3
_DEFAULT_ADX_PERIOD = 14
_DEFAULT_BB_PERIOD = 20


def analyze_structure(
    snapshot: MarketSnapshot,
    *,
    session_window: SessionWindow | None = None,
    swing_lookback: int = _DEFAULT_SWING_LOOKBACK,
) -> StructureAnalysis:
    window = session_window or SessionWindow()
    underlying = snapshot.underlying_snapshot

    if underlying is None:
        empty_state = build_structure_state([])
        empty_regime = classify_regime(
            trend_bias=empty_state.trend_bias,
            bias_evidence=empty_state.bias_evidence,
            adx_last=None,
            bb_width_now=None,
            bb_width_avg=None,
            nearest_key_reaction=None,
        )
        return StructureAnalysis(
            structure=empty_state,
            levels=SessionLevels(),
            liquidity_zones=(),
            level_reactions=(),
            regime=empty_regime,
            vwap=None,
            vwap_relation=None,
        )

    levels = compute_session_levels(underlying, as_of=snapshot.as_of, session_window=window)

    m1 = underlying.candles.get(Timeframe.M1, ())
    swings = find_swings(m1, as_of=snapshot.as_of, lookback=swing_lookback) if m1 else []
    structure_state = build_structure_state(swings)

    zones = identify_liquidity_zones(levels, structure_state.swings, snapshot.options)

    ltp = underlying.ltp.value
    reactions: tuple[LevelReaction, ...] = ()
    nearest_reaction: ReactionKind | None = None
    if ltp is not None and zones:
        reactions = tuple(classify_reactions_at_zones(zones, m1, current_price=ltp))
        tested = [r for r in reactions if r.reaction is not ReactionKind.UNTESTED]
        if tested:
            nearest = min(tested, key=lambda r: abs(r.zone.level - ltp))
            nearest_reaction = nearest.reaction

    closes = [c.close for c in sorted((c for c in m1 if c.is_closed), key=lambda c: c.start)]
    adx_values = adx_series(
        sorted((c for c in m1 if c.is_closed), key=lambda c: c.start), period=_DEFAULT_ADX_PERIOD
    )
    adx_last = next((v for v in reversed(adx_values) if v is not None), None)

    bb_width_now: float | None = None
    bb_width_avg: float | None = None
    if len(closes) >= _DEFAULT_BB_PERIOD * 2:
        upper, mid, lower = bollinger_bands(closes, period=_DEFAULT_BB_PERIOD)
        widths = [
            (up - low)
            for up, low in zip(upper, lower, strict=True)
            if up is not None and low is not None
        ]
        if widths:
            bb_width_now = widths[-1]
            bb_width_avg = sum(widths) / len(widths)

    regime = classify_regime(
        trend_bias=structure_state.trend_bias,
        bias_evidence=structure_state.bias_evidence,
        adx_last=adx_last,
        bb_width_now=bb_width_now,
        bb_width_avg=bb_width_avg,
        nearest_key_reaction=nearest_reaction,
    )

    today_closed_m1 = sorted(
        (c for c in m1 if c.is_closed and c.start <= snapshot.as_of), key=lambda c: c.start
    )
    session_vwap: float | None = None
    as_of_ist_date = snapshot.as_of.astimezone(IST).date()
    session_open, _ = window.session_bounds(as_of_ist_date)
    today_only = [c for c in today_closed_m1 if c.start >= session_open]
    if today_only:
        vwap_values = vwap_series(today_only)
        session_vwap = vwap_values[-1] if vwap_values else None

    vwap_relation: str | None = None
    if session_vwap is not None and ltp is not None:
        if abs(ltp - session_vwap) < 1e-9:
            vwap_relation = "AT"
        else:
            vwap_relation = "ABOVE" if ltp > session_vwap else "BELOW"

    return StructureAnalysis(
        structure=structure_state,
        levels=levels,
        liquidity_zones=tuple(zones),
        level_reactions=reactions,
        regime=regime,
        vwap=session_vwap,
        vwap_relation=vwap_relation,
    )
