from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.config.session import SessionWindow
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import OHLC, UnderlyingSnapshot
from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.structure.levels import compute_session_levels

SESSION_OPEN_UTC = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST, Friday


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=SESSION_OPEN_UTC, fetched_at=SESSION_OPEN_UTC)


def _m1(offset: int, o: float, h: float, low: float, c: float) -> Candle:
    start = SESSION_OPEN_UTC + timedelta(minutes=offset)
    return Candle(
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=100,
        is_closed=True,
        source="raw",
    )


def test_today_levels_fall_back_to_m1_when_day_ohlc_missing() -> None:
    candles = [_m1(i, 100, 100 + i, 100 - i, 100) for i in range(20)]
    underlying = UnderlyingSnapshot(
        symbol="NIFTY",
        ltp=_sf(100),
        day_ohlc=OHLC(open=_sf(None), high=_sf(None), low=_sf(None), volume=_sf(None), close=_sf(None)),
        prev_day_ohlc=None,
        prev_week_ohlc=None,
        candles={Timeframe.M1: tuple(candles)},
    )
    as_of = SESSION_OPEN_UTC + timedelta(minutes=20)
    levels = compute_session_levels(underlying, as_of=as_of, session_window=SessionWindow())

    assert levels.today_high == max(c.high for c in candles)
    assert levels.today_low == min(c.low for c in candles)
    assert levels.today_open == candles[0].open
    assert levels.session_midpoint == (levels.today_high + levels.today_low) / 2


def test_opening_range_computed_from_first_n_minutes() -> None:
    candles = [_m1(i, 100, 105 if i < 15 else 200, 95 if i < 15 else 50, 100) for i in range(30)]
    underlying = UnderlyingSnapshot(
        symbol="NIFTY",
        ltp=_sf(100),
        day_ohlc=OHLC(open=_sf(None), high=_sf(None), low=_sf(None), volume=_sf(None), close=_sf(None)),
        prev_day_ohlc=None,
        prev_week_ohlc=None,
        candles={Timeframe.M1: tuple(candles)},
    )
    as_of = SESSION_OPEN_UTC + timedelta(minutes=30)
    levels = compute_session_levels(
        underlying, as_of=as_of, session_window=SessionWindow(), opening_range_minutes=15
    )
    assert levels.opening_range_high == 105
    assert levels.opening_range_low == 95


def test_prev_day_from_field_when_present() -> None:
    prev_day = OHLC(open=_sf(200), high=_sf(210), low=_sf(190), close=_sf(205), volume=_sf(None))
    underlying = UnderlyingSnapshot(
        symbol="NIFTY",
        ltp=_sf(100),
        day_ohlc=OHLC(open=_sf(100), high=_sf(110), low=_sf(90), close=_sf(None), volume=_sf(None)),
        prev_day_ohlc=prev_day,
        prev_week_ohlc=None,
        candles={},
    )
    levels = compute_session_levels(underlying, as_of=SESSION_OPEN_UTC, session_window=SessionWindow())
    assert levels.prev_day_high == 210
    assert levels.prev_day_low == 190
    assert levels.prev_day_close == 205


def test_missing_data_stays_none_not_fabricated() -> None:
    underlying = UnderlyingSnapshot(
        symbol="NIFTY",
        ltp=_sf(100),
        day_ohlc=OHLC(open=_sf(None), high=_sf(None), low=_sf(None), close=_sf(None), volume=_sf(None)),
        prev_day_ohlc=None,
        prev_week_ohlc=None,
        candles={},
    )
    levels = compute_session_levels(underlying, as_of=SESSION_OPEN_UTC, session_window=SessionWindow())
    assert levels.today_high is None
    assert levels.prev_day_high is None
    assert levels.current_week_high is None
    assert levels.session_midpoint is None
