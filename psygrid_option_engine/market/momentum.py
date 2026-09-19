"""Impulse/momentum quality metrics (brief section 10).

Pure functions over a trailing window of closed candles — evidence for
`authorization/`, never a decision by itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from psygrid_option_engine.domain.timeframe import Candle


@dataclass(frozen=True)
class ImpulseMetrics:
    size: float  # signed price change over the window (last close - first open)
    duration_bars: int
    velocity: float  # size / duration_bars
    atr_normalized_move: float | None  # size / ATR; None if ATR unavailable
    volume_expansion_ratio: float | None  # avg volume this window / avg volume of the prior window
    candle_efficiency: float | None  # mean |close-open|/(high-low): how "clean" the move is
    follow_through: bool | None  # does the final bar's own direction match the window's overall direction?
    acceleration: float | None  # 2nd-half velocity minus 1st-half velocity


class MomentumQuality(StrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    DECAYING = "DECAYING"


def compute_impulse(
    candles: Sequence[Candle], *, window: int = 5, atr_value: float | None = None
) -> ImpulseMetrics | None:
    closed = sorted((c for c in candles if c.is_closed), key=lambda c: c.start)
    if len(closed) < window:
        return None
    recent = closed[-window:]
    prior = closed[-2 * window : -window] if len(closed) >= 2 * window else []

    size = recent[-1].close - recent[0].open
    velocity = size / window

    atr_norm = (size / atr_value) if atr_value not in (None, 0) else None

    recent_known_vol = [c.volume for c in recent if c.volume is not None]
    prior_known_vol = [c.volume for c in prior if c.volume is not None]
    volume_expansion = None
    if recent_known_vol and prior_known_vol:
        prior_avg = sum(prior_known_vol) / len(prior_known_vol)
        if prior_avg > 0:
            volume_expansion = (sum(recent_known_vol) / len(recent_known_vol)) / prior_avg

    efficiencies = [abs(c.close - c.open) / (c.high - c.low) for c in recent if c.high > c.low]
    efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else None

    last = recent[-1]
    if size != 0 and last.close != last.open:
        follow_through = (last.close - last.open > 0) == (size > 0)
    else:
        follow_through = None

    half = window // 2
    acceleration: float | None = None
    if half >= 1 and window - half >= 1:
        first_half_velocity = (recent[half - 1].close - recent[0].open) / half
        second_half_velocity = (recent[-1].close - recent[half].open) / (window - half)
        acceleration = second_half_velocity - first_half_velocity

    return ImpulseMetrics(
        size=size,
        duration_bars=window,
        velocity=velocity,
        atr_normalized_move=atr_norm,
        volume_expansion_ratio=volume_expansion,
        candle_efficiency=efficiency,
        follow_through=follow_through,
        acceleration=acceleration,
    )


def assess_momentum_quality(metrics: ImpulseMetrics | None) -> MomentumQuality | None:
    """A simple, documented point-scoring rule — not a statistically
    calibrated model (brief section 33: nothing here is presented as a
    calibrated probability)."""
    if metrics is None:
        return None

    score = 0
    signals_available = 0

    if metrics.atr_normalized_move is not None:
        signals_available += 1
        if abs(metrics.atr_normalized_move) >= 1.5:
            score += 1

    if metrics.volume_expansion_ratio is not None:
        signals_available += 1
        if metrics.volume_expansion_ratio >= 1.3:
            score += 1

    if metrics.candle_efficiency is not None:
        signals_available += 1
        if metrics.candle_efficiency >= 0.6:
            score += 1

    if metrics.follow_through is not None:
        signals_available += 1
        if metrics.follow_through:
            score += 1

    if metrics.acceleration is not None and metrics.size != 0:
        signals_available += 1
        # acceleration in the same direction as the overall move -> still building
        if (metrics.acceleration > 0) == (metrics.size > 0):
            score += 1
        else:
            score -= 1  # decelerating

    if signals_available == 0:
        return None

    ratio = score / signals_available
    if ratio >= 0.75:
        return MomentumQuality.STRONG
    if ratio >= 0.4:
        return MomentumQuality.MODERATE
    if ratio >= 0:
        return MomentumQuality.WEAK
    return MomentumQuality.DECAYING
