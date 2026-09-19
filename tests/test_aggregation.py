from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from psygrid_option_engine.config.session import SessionWindow
from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.market.aggregation import (
    aggregate_daily,
    aggregate_intraday,
    aggregate_weekly,
    latest_closed,
)

# 2026-09-18 (Friday) 09:15 IST == 03:45 UTC
SESSION_OPEN_UTC = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _m1(offset_minutes: int, o: float, h: float, low: float, c: float, v: float = 100) -> Candle:
    start = SESSION_OPEN_UTC + timedelta(minutes=offset_minutes)
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


def test_aggregate_intraday_groups_aligned_to_session_open() -> None:
    candles = [_m1(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i + 0.5) for i in range(10)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)
    result = aggregate_intraday(candles, Timeframe.M5, as_of=as_of)

    assert len(result) == 2
    first = result[0]
    assert first.start == SESSION_OPEN_UTC
    assert first.end == SESSION_OPEN_UTC + timedelta(minutes=5)
    assert first.open == candles[0].open
    assert first.close == candles[4].close
    assert first.high == max(c.high for c in candles[:5])
    assert first.low == min(c.low for c in candles[:5])
    assert first.volume == 500
    assert first.is_closed is True


def test_aggregate_intraday_forming_bucket_not_closed() -> None:
    candles = [_m1(i, 100, 101, 99, 100) for i in range(3)]  # only 3 of 5 minutes
    as_of = SESSION_OPEN_UTC + timedelta(minutes=3)
    result = aggregate_intraday(candles, Timeframe.M5, as_of=as_of)

    assert len(result) == 1
    assert result[0].is_closed is False


def test_aggregate_intraday_drops_future_candles() -> None:
    candles = [_m1(i, 100, 101, 99, 100) for i in range(10)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=4)  # only first bucket should be visible
    result = aggregate_intraday(candles, Timeframe.M5, as_of=as_of)

    assert len(result) == 1
    assert result[0].start == SESSION_OPEN_UTC


def test_aggregate_intraday_rejects_mixed_timeframes() -> None:
    m1 = _m1(0, 100, 101, 99, 100)
    m5 = Candle(
        timeframe=Timeframe.M5,
        start=SESSION_OPEN_UTC,
        end=SESSION_OPEN_UTC + timedelta(minutes=5),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        is_closed=True,
        source="raw",
    )
    with pytest.raises(ValueError):
        aggregate_intraday([m1, m5], Timeframe.M15, as_of=SESSION_OPEN_UTC + timedelta(hours=1))


def test_aggregate_intraday_rejects_coarser_base() -> None:
    h1 = Candle(
        timeframe=Timeframe.H1,
        start=SESSION_OPEN_UTC,
        end=SESSION_OPEN_UTC + timedelta(hours=1),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        is_closed=True,
        source="raw",
    )
    with pytest.raises(ValueError):
        aggregate_intraday([h1], Timeframe.M5, as_of=SESSION_OPEN_UTC + timedelta(hours=2))


def test_aggregate_daily_uses_session_bounds() -> None:
    candles = [_m1(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(0, 300, 30)]
    as_of = SESSION_OPEN_UTC + timedelta(hours=5)  # 14:15 IST, before the 15:30 close
    window = SessionWindow()
    result = aggregate_daily(candles, as_of=as_of, session_window=window)

    assert len(result) == 1
    assert result[0].timeframe == Timeframe.D1
    assert result[0].start == SESSION_OPEN_UTC
    assert result[0].is_closed is False  # as_of is before session close


def test_aggregate_daily_closes_after_session_close() -> None:
    candles = [_m1(0, 100, 101, 99, 100)]
    window = SessionWindow()
    as_of = SESSION_OPEN_UTC + timedelta(hours=12)  # well past 15:30 IST close
    result = aggregate_daily(candles, as_of=as_of, session_window=window)
    assert result[0].is_closed is True


def test_aggregate_weekly_spans_monday_to_friday() -> None:
    # 2026-09-18 is a Friday within ISO week (2026, 38)
    candles = [_m1(0, 100, 101, 99, 100)]
    window = SessionWindow()
    as_of = SESSION_OPEN_UTC + timedelta(hours=12)
    result = aggregate_weekly(candles, as_of=as_of, session_window=window)
    assert len(result) == 1
    assert result[0].timeframe == Timeframe.W1
    assert result[0].is_closed is True  # Friday close has passed


def test_latest_closed_ignores_forming_bar() -> None:
    closed = _m1(0, 100, 101, 99, 100)
    forming = Candle(
        timeframe=Timeframe.M1,
        start=SESSION_OPEN_UTC + timedelta(minutes=1),
        end=SESSION_OPEN_UTC + timedelta(minutes=2),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        is_closed=False,
        source="raw",
    )
    assert latest_closed([closed, forming]) is closed


def test_latest_closed_empty() -> None:
    assert latest_closed([]) is None
