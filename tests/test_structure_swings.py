from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.structure.swings import build_structure_state, find_swings
from psygrid_option_engine.structure.types import StructureLabel, SwingKind, TrendBias

START = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _candle(i: int, h: float, low: float, c: float) -> Candle:
    start = START + timedelta(minutes=i)
    return Candle(
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=c,
        high=h,
        low=low,
        close=c,
        volume=100,
        is_closed=True,
        source="raw",
    )


def _zigzag(highs: list[float], lows: list[float]) -> list[Candle]:
    # interleave: candle i is a "high" bar with value highs[i//2] on even i,
    # and a "low" bar with value lows[i//2] on odd i, building an obvious
    # alternating swing pattern lookback=1 can detect.
    candles = []
    i = 0
    for h, low in zip(highs, lows, strict=True):
        candles.append(_candle(i, h, h - 5, h - 2))
        i += 1
        candles.append(_candle(i, low + 5, low, low + 2))
        i += 1
    return candles


def test_find_swings_detects_simple_peak() -> None:
    # candles: low, low, HIGH, low, low -> index 2 should be a swing high
    candles = [
        _candle(0, 10, 8, 9),
        _candle(1, 11, 9, 10),
        _candle(2, 20, 18, 19),
        _candle(3, 11, 9, 10),
        _candle(4, 10, 8, 9),
    ]
    as_of = candles[-1].end
    swings = find_swings(candles, as_of=as_of, lookback=2)
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    assert len(highs) == 1
    assert highs[0].price == 20
    assert highs[0].index == 2


def test_find_swings_respects_no_lookahead() -> None:
    candles = [
        _candle(0, 10, 8, 9),
        _candle(1, 11, 9, 10),
        _candle(2, 20, 18, 19),
        _candle(3, 11, 9, 10),
        _candle(4, 10, 8, 9),
    ]
    # as_of right after the peak candle closes, before the 2 confirming
    # bars after it exist -> the swing must NOT be reported yet.
    as_of = candles[2].end
    swings = find_swings(candles, as_of=as_of, lookback=2)
    assert swings == []


def test_uptrend_produces_hh_hl_bullish_bias() -> None:
    # steadily rising peaks and troughs
    candles = _zigzag(highs=[20, 25, 30, 35, 40], lows=[8, 12, 16, 20, 24])
    as_of = candles[-1].end
    swings = find_swings(candles, as_of=as_of, lookback=1)
    state = build_structure_state(swings)
    labels = [ls.label for ls in state.swings if ls.label is not None]
    assert StructureLabel.HH in labels
    assert StructureLabel.HL in labels
    assert StructureLabel.LH not in labels
    assert StructureLabel.LL not in labels
    assert state.trend_bias is TrendBias.BULLISH


def test_downtrend_produces_lh_ll_bearish_bias() -> None:
    candles = _zigzag(highs=[40, 35, 30, 25, 20], lows=[24, 20, 16, 12, 8])
    as_of = candles[-1].end
    swings = find_swings(candles, as_of=as_of, lookback=1)
    state = build_structure_state(swings)
    labels = [ls.label for ls in state.swings if ls.label is not None]
    assert StructureLabel.LH in labels
    assert StructureLabel.LL in labels
    assert state.trend_bias is TrendBias.BEARISH


def test_invalidation_levels_track_most_recent_confirmed_swing() -> None:
    candles = _zigzag(highs=[20, 25, 30], lows=[8, 12, 16])
    as_of = candles[-1].end
    swings = find_swings(candles, as_of=as_of, lookback=1)
    state = build_structure_state(swings)
    # the final low (16, at the very last candle) isn't confirmable yet -
    # it has no bar after it - so the most recent *confirmed* low (12) is
    # the invalidation level, not the raw last-candle low.
    assert state.invalidation_long == 12
    assert state.invalidation_short == 30


def test_no_swings_yields_neutral_state() -> None:
    state = build_structure_state([])
    assert state.trend_bias is TrendBias.NEUTRAL
    assert state.invalidation_long is None
    assert state.invalidation_short is None
