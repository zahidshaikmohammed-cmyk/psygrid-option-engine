from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.market.momentum import MomentumQuality, assess_momentum_quality, compute_impulse

START = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _candle(i: int, o: float, h: float, low: float, c: float, v: float | None = 1000) -> Candle:
    start = START + timedelta(minutes=i)
    return Candle(
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        is_closed=True,
        source="raw",
    )


def test_compute_impulse_needs_full_window() -> None:
    candles = [_candle(i, 100, 101, 99, 100) for i in range(3)]
    assert compute_impulse(candles, window=5) is None


def test_compute_impulse_basic_size_and_velocity() -> None:
    candles = [_candle(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i + 1) for i in range(5)]
    metrics = compute_impulse(candles, window=5, atr_value=2.0)
    assert metrics is not None
    assert metrics.size == candles[-1].close - candles[0].open
    assert metrics.velocity == metrics.size / 5
    assert metrics.atr_normalized_move == metrics.size / 2.0


def test_compute_impulse_volume_expansion() -> None:
    prior = [_candle(i, 100, 101, 99, 100, v=500) for i in range(5)]
    recent = [_candle(i + 5, 100, 101, 99, 100, v=1500) for i in range(5)]
    metrics = compute_impulse(prior + recent, window=5)
    assert metrics is not None
    assert metrics.volume_expansion_ratio == 3.0


def test_compute_impulse_follow_through_true_when_last_bar_agrees() -> None:
    candles = [_candle(i, 100, 105, 95, 100 + i * 2) for i in range(5)]  # rising closes
    metrics = compute_impulse(candles, window=5)
    assert metrics is not None
    assert metrics.follow_through is True


def test_assess_momentum_quality_strong_case() -> None:
    candles = [
        _candle(i, 100 + i * 3, 100 + i * 3 + 3.5, 100 + i * 3 - 0.2, 100 + i * 3 + 3, v=200 * (i + 1))
        for i in range(5)
    ]
    metrics = compute_impulse(candles, window=5, atr_value=1.0)
    quality = assess_momentum_quality(metrics)
    assert quality in (MomentumQuality.STRONG, MomentumQuality.MODERATE)


def test_assess_momentum_quality_none_when_no_metrics() -> None:
    assert assess_momentum_quality(None) is None
