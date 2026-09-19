"""Liquidity-zone identification and price-reaction classification
(brief section 9).

Reaction classification uses only closed-candle OHLC relative to a level —
it never claims tick-level order-flow information the available data
cannot support ("Never claim invisible order-flow information", brief
section 9).
"""

from __future__ import annotations

from collections.abc import Sequence

from psygrid_option_engine.domain.snapshot import OptionChainSnapshot
from psygrid_option_engine.domain.timeframe import Candle
from psygrid_option_engine.structure.types import (
    LabeledSwing,
    LevelReaction,
    LiquidityKind,
    LiquidityZone,
    ReactionKind,
    SessionLevels,
    SwingKind,
)

_EQUAL_LEVEL_TOLERANCE_PCT = 0.0008  # ~0.08%: close enough to call two swings "equal"


def identify_liquidity_zones(
    levels: SessionLevels,
    swings: Sequence[LabeledSwing],
    option_chain: OptionChainSnapshot | None = None,
    *,
    oi_wall_count: int = 2,
) -> list[LiquidityZone]:
    zones: list[LiquidityZone] = []

    def _add(kind: LiquidityKind, level: float | None, note: str) -> None:
        if level is not None:
            zones.append(LiquidityZone(kind=kind, level=level, note=note))

    _add(LiquidityKind.PDH, levels.prev_day_high, "previous day high")
    _add(LiquidityKind.PDL, levels.prev_day_low, "previous day low")
    _add(LiquidityKind.PWH, levels.prev_week_high, "previous week high")
    _add(LiquidityKind.PWL, levels.prev_week_low, "previous week low")
    _add(LiquidityKind.SESSION_HIGH, levels.today_high, "session high so far")
    _add(LiquidityKind.SESSION_LOW, levels.today_low, "session low so far")
    _add(LiquidityKind.OPENING_RANGE_HIGH, levels.opening_range_high, "opening range high")
    _add(LiquidityKind.OPENING_RANGE_LOW, levels.opening_range_low, "opening range low")

    highs = sorted(ls.swing.price for ls in swings if ls.swing.kind is SwingKind.HIGH)
    lows = sorted(ls.swing.price for ls in swings if ls.swing.kind is SwingKind.LOW)
    zones.extend(_equal_level_zones(highs, LiquidityKind.EQUAL_HIGH, "equal highs"))
    zones.extend(_equal_level_zones(lows, LiquidityKind.EQUAL_LOW, "equal lows"))

    if option_chain is not None:
        ce_legs = [leg for leg in option_chain.legs if leg.option_type == "CE" and leg.oi.available]
        pe_legs = [leg for leg in option_chain.legs if leg.option_type == "PE" and leg.oi.available]
        for leg in sorted(ce_legs, key=lambda leg: leg.oi.value or 0, reverse=True)[:oi_wall_count]:
            _add(LiquidityKind.OI_WALL_CE, leg.strike, f"CE OI concentration ({leg.oi.value:.0f})")
        for leg in sorted(pe_legs, key=lambda leg: leg.oi.value or 0, reverse=True)[:oi_wall_count]:
            _add(LiquidityKind.OI_WALL_PE, leg.strike, f"PE OI concentration ({leg.oi.value:.0f})")

    return zones


def _equal_level_zones(prices: Sequence[float], kind: LiquidityKind, label: str) -> list[LiquidityZone]:
    zones: list[LiquidityZone] = []
    used: set[int] = set()
    for i in range(len(prices)):
        if i in used:
            continue
        cluster = [prices[i]]
        cluster_idx = [i]
        for j in range(i + 1, len(prices)):
            if j in used:
                continue
            if abs(prices[j] - prices[i]) / max(abs(prices[i]), 1e-9) <= _EQUAL_LEVEL_TOLERANCE_PCT:
                cluster.append(prices[j])
                cluster_idx.append(j)
        if len(cluster) >= 2:
            used.update(cluster_idx)
            avg = sum(cluster) / len(cluster)
            note = f"{label} ({len(cluster)}x near {avg:.2f})"
            zones.append(LiquidityZone(kind=kind, level=avg, note=note))
    return zones


def classify_reaction(
    recent_closed_candles: Sequence[Candle], level: float, *, direction: str
) -> ReactionKind:
    """`direction`: "ABOVE" evaluates a resistance test (breaking up
    through `level`); "BELOW" evaluates a support test (breaking down
    through `level`)."""
    if direction not in ("ABOVE", "BELOW"):
        raise ValueError("direction must be 'ABOVE' or 'BELOW'")
    if not recent_closed_candles:
        return ReactionKind.UNTESTED

    if direction == "ABOVE":
        touched = any(c.high >= level for c in recent_closed_candles)
    else:
        touched = any(c.low <= level for c in recent_closed_candles)
    if not touched:
        return ReactionKind.UNTESTED

    last = recent_closed_candles[-1]

    if direction == "ABOVE":
        closed_above_earlier = any(c.close > level for c in recent_closed_candles[:-1])
        if closed_above_earlier and last.close < level:
            return ReactionKind.FAILED_BREAK
        pierced = any(c.high > level for c in recent_closed_candles)
        closed_above = last.close > level
        if closed_above and last.low > level:
            return ReactionKind.ACCEPTANCE
        if pierced and not closed_above:
            max_pierce_pct = (max(c.high for c in recent_closed_candles) - level) / level
            return ReactionKind.SWEEP if max_pierce_pct > 0.001 else ReactionKind.REJECTION
        if closed_above:
            return ReactionKind.CONTINUATION
        return ReactionKind.REJECTION

    # direction == "BELOW"
    closed_below_earlier = any(c.close < level for c in recent_closed_candles[:-1])
    if closed_below_earlier and last.close > level:
        return ReactionKind.FAILED_BREAK
    pierced = any(c.low < level for c in recent_closed_candles)
    closed_below = last.close < level
    if closed_below and last.high < level:
        return ReactionKind.ACCEPTANCE
    if pierced and not closed_below:
        max_pierce_pct = (level - min(c.low for c in recent_closed_candles)) / level
        return ReactionKind.SWEEP if max_pierce_pct > 0.001 else ReactionKind.REJECTION
    if closed_below:
        return ReactionKind.CONTINUATION
    return ReactionKind.REJECTION


def classify_reactions_at_zones(
    zones: Sequence[LiquidityZone], candles: Sequence[Candle], *, current_price: float, window: int = 10
) -> list[LevelReaction]:
    closed = sorted([c for c in candles if c.is_closed], key=lambda c: c.start)[-window:]
    reactions: list[LevelReaction] = []
    for zone in zones:
        # zone below current price -> it's acting as support, tested from
        # above (direction="BELOW"); zone above current price -> resistance,
        # tested from below (direction="ABOVE").
        direction = "BELOW" if zone.level <= current_price else "ABOVE"
        reaction = classify_reaction(closed, zone.level, direction=direction)
        reactions.append(LevelReaction(zone=zone, reaction=reaction))
    return reactions
