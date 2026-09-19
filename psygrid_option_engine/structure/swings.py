"""Swing-point detection and HH/HL/LH/LL structure classification.

A swing at index `i` (a fractal: its high/low is the extreme among the
`lookback` candles on each side) is only "confirmed" once those
`lookback` candles after it exist and are closed — `confirmed_at` records
that instant, and `find_swings` drops any swing whose `confirmed_at` is
after `as_of`. This is the no-lookahead rule applied to swing detection
specifically: you cannot know a bar was a swing high until enough bars
have printed after it (docs/ARCHITECTURE.md section 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from psygrid_option_engine.domain.timeframe import Candle
from psygrid_option_engine.structure.types import (
    LabeledSwing,
    StructureLabel,
    StructureState,
    SwingKind,
    SwingPoint,
    TrendBias,
)


def find_swings(candles: Sequence[Candle], *, as_of: datetime, lookback: int = 3) -> list[SwingPoint]:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    closed = sorted([c for c in candles if c.is_closed and c.start <= as_of], key=lambda c: c.start)
    n = len(closed)
    swings: list[SwingPoint] = []

    for i in range(lookback, n - lookback):
        window = closed[i - lookback : i + lookback + 1]
        candidate = closed[i]
        confirmed_at = closed[i + lookback].end
        if confirmed_at > as_of:
            continue
        if candidate.high == max(c.high for c in window):
            swings.append(
                SwingPoint(
                    kind=SwingKind.HIGH,
                    index=i,
                    time=candidate.start,
                    price=candidate.high,
                    confirmed_at=confirmed_at,
                )
            )
        if candidate.low == min(c.low for c in window):
            swings.append(
                SwingPoint(
                    kind=SwingKind.LOW,
                    index=i,
                    time=candidate.start,
                    price=candidate.low,
                    confirmed_at=confirmed_at,
                )
            )
    return swings


def classify_structure(swings: Sequence[SwingPoint]) -> list[LabeledSwing]:
    ordered = sorted(swings, key=lambda s: s.index)
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    labeled: list[LabeledSwing] = []

    for s in ordered:
        if s.kind is SwingKind.HIGH:
            label = None if last_high is None else (
                StructureLabel.HH if s.price > last_high.price else StructureLabel.LH
            )
            last_high = s
        else:
            label = None if last_low is None else (
                StructureLabel.HL if s.price > last_low.price else StructureLabel.LL
            )
            last_low = s
        labeled.append(LabeledSwing(swing=s, label=label))
    return labeled


def build_structure_state(swings: Sequence[SwingPoint], *, lookback_labels: int = 4) -> StructureState:
    labeled = classify_structure(swings)
    recent_labels = [ls.label for ls in labeled if ls.label is not None][-lookback_labels:]

    bullish_votes = sum(1 for lbl in recent_labels if lbl in (StructureLabel.HH, StructureLabel.HL))
    bearish_votes = sum(1 for lbl in recent_labels if lbl in (StructureLabel.LH, StructureLabel.LL))

    evidence: list[str] = []
    if not recent_labels:
        bias = TrendBias.NEUTRAL
        evidence.append("no confirmed swing sequence yet")
    elif bullish_votes > bearish_votes:
        bias = TrendBias.BULLISH
        evidence.append(f"{bullish_votes}/{len(recent_labels)} recent swings are HH/HL")
    elif bearish_votes > bullish_votes:
        bias = TrendBias.BEARISH
        evidence.append(f"{bearish_votes}/{len(recent_labels)} recent swings are LH/LL")
    else:
        bias = TrendBias.NEUTRAL
        evidence.append("recent swings split evenly between HH/HL and LH/LL")

    last_low = next((ls.swing for ls in reversed(labeled) if ls.swing.kind is SwingKind.LOW), None)
    last_high = next((ls.swing for ls in reversed(labeled) if ls.swing.kind is SwingKind.HIGH), None)

    return StructureState(
        swings=tuple(labeled),
        trend_bias=bias,
        bias_evidence=tuple(evidence),
        invalidation_long=last_low.price if last_low else None,
        invalidation_short=last_high.price if last_high else None,
    )
