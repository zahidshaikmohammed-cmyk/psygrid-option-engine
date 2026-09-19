from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.market.indicators import (
    _wilder_smooth,
    adx,
    atr,
    bollinger_bands,
    donchian_channel,
    ema,
    macd,
    rsi,
    rvol,
    sma,
    supertrend,
    vwap,
)

START = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _candle(i: int, h: float, low: float, c: float, o: float | None = None, v: float | None = 100) -> Candle:
    start = START + timedelta(minutes=i)
    return Candle(
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=o if o is not None else c,
        high=h,
        low=low,
        close=c,
        volume=v,
        is_closed=True,
        source="raw",
    )


def test_sma_basic() -> None:
    out = sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2, 3, 4]


def test_sma_rejects_nonpositive_period() -> None:
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)


def test_ema_matches_sma_on_linear_series() -> None:
    out = ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_wilder_smooth_hand_computed() -> None:
    out = _wilder_smooth([2, 2, 2, 8, 2], 3)
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(4.0)  # (2*2 + 8) / 3
    assert out[4] == pytest.approx(10 / 3)  # (4*2 + 2) / 3


def test_atr_constant_true_range() -> None:
    candles = [
        _candle(0, 10, 8, 9),
        _candle(1, 11, 9, 10),
        _candle(2, 12, 10, 11),
    ]
    out = atr(candles, period=3)
    assert out[2] == pytest.approx(2.0)


def test_rsi_monotonic_up_is_100() -> None:
    out = rsi([1, 2, 3, 4, 5], period=3)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(100.0)
    assert out[4] == pytest.approx(100.0)


def test_rsi_monotonic_down_is_0() -> None:
    out = rsi([5, 4, 3, 2, 1], period=3)
    assert out[3] == pytest.approx(0.0)
    assert out[4] == pytest.approx(0.0)


def test_rsi_flat_is_50() -> None:
    out = rsi([5, 5, 5, 5, 5], period=3)
    assert out[3] == pytest.approx(50.0)


def test_macd_warmup_alignment() -> None:
    closes = [float(i) for i in range(1, 40)]
    macd_line, signal_line, hist = macd(closes, fast=5, slow=10, signal=3)
    assert all(v is None for v in macd_line[:9])
    assert macd_line[9] is not None
    # steadily rising closes -> fast EMA pulls ahead of slow EMA -> positive MACD
    assert macd_line[-1] > 0
    assert hist[-1] is not None


def test_rvol_baseline_excludes_current_bar() -> None:
    volumes = [10.0] * 20 + [50.0]
    out = rvol(volumes, period=20)
    assert out[20] == pytest.approx(5.0)
    assert out[19] is None


def test_bollinger_bands_hand_computed() -> None:
    upper, mid, lower = bollinger_bands([1, 2, 3, 4, 5], period=3, num_std=1.0)
    assert mid[2] == pytest.approx(2.0)
    assert upper[2] == pytest.approx(2.0 + 0.8164965809277260)
    assert lower[2] == pytest.approx(2.0 - 0.8164965809277260)


def test_donchian_channel_hand_computed() -> None:
    candles = [
        _candle(0, 10, 5, 8),
        _candle(1, 12, 6, 9),
        _candle(2, 9, 4, 7),
        _candle(3, 15, 7, 12),
    ]
    upper, lower = donchian_channel(candles, period=3)
    assert upper[2] == 12
    assert lower[2] == 4
    assert upper[3] == 15
    assert lower[3] == 4


def test_supertrend_uptrend_direction() -> None:
    candles = [_candle(i, 100 + i * 2, 95 + i * 2, 99 + i * 2) for i in range(20)]
    value, direction = supertrend(candles, period=5, multiplier=2.0)
    assert direction[-1] == "UP"
    assert value[-1] is not None
    assert value[-1] < candles[-1].close


def test_supertrend_downtrend_direction() -> None:
    candles = [_candle(i, 100 - i * 2, 95 - i * 2, 99 - i * 2) for i in range(20)]
    value, direction = supertrend(candles, period=5, multiplier=2.0)
    assert direction[-1] == "DOWN"
    assert value[-1] > candles[-1].close


def test_adx_flat_series_stays_none() -> None:
    candles = [_candle(i, 10, 10, 10) for i in range(20)]
    out = adx(candles, period=5)
    assert all(v is None for v in out)  # zero true range throughout -> undefined


def test_adx_strong_trend_produces_high_value() -> None:
    candles = [_candle(i, 100 + i * 3, 98 + i * 3, 99 + i * 3) for i in range(30)]
    out = adx(candles, period=5)
    last = [v for v in out if v is not None][-1]
    assert last > 20


def test_vwap_hand_computed() -> None:
    candles = [
        _candle(0, 10, 8, 9, v=100),
        _candle(1, 12, 10, 11, v=200),
    ]
    out = vwap(candles)
    assert out[0] == pytest.approx(9.0)
    assert out[1] == pytest.approx((9 * 100 + 11 * 200) / 300)


def test_vwap_zero_volume_falls_back_to_typical_price() -> None:
    candles = [_candle(0, 10, 8, 9, v=0)]
    out = vwap(candles)
    assert out[0] == pytest.approx(9.0)
