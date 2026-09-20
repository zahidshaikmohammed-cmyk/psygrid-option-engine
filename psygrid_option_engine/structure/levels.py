"""Daily/weekly/intraday structural levels (brief section 8).

Honesty note: a single live snapshot typically only carries the current
day's M1 candles plus whatever `prev_day`/`prev_week` fields the
underlying endpoint exposes directly (docs/ENDPOINTS.md — unverified).
There is no multi-day history to aggregate from in a single fetch, so
`current_week_high/low` and anything needing more than today's data will
often legitimately be `None` in live mode until `replay/` (Phase 11)
accumulates history across sessions, or the upstream turns out to expose
it directly. This module never fabricates a value to fill that gap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from psygrid_option_engine.config.session import IST, SessionWindow
from psygrid_option_engine.domain.snapshot import UnderlyingSnapshot
from psygrid_option_engine.domain.timeframe import Timeframe
from psygrid_option_engine.market.aggregation import aggregate_weekly
from psygrid_option_engine.structure.types import SessionLevels


def _to_ist_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST).date()


def compute_session_levels(
    underlying: UnderlyingSnapshot,
    *,
    as_of: datetime,
    session_window: SessionWindow | None = None,
    opening_range_minutes: int | None = None,
) -> SessionLevels:
    window = session_window or SessionWindow()
    or_minutes = opening_range_minutes if opening_range_minutes is not None else window.opening_period_minutes

    today = _to_ist_date(as_of)
    session_open, _ = window.session_bounds(today)

    m1 = underlying.candles.get(Timeframe.M1, ())
    today_closed_m1 = sorted(
        (c for c in m1 if c.is_closed and c.start <= as_of and _to_ist_date(c.start) == today),
        key=lambda c: c.start,
    )

    today_high = underlying.day_ohlc.high.value
    if today_high is None and today_closed_m1:
        today_high = max(c.high for c in today_closed_m1)
    today_low = underlying.day_ohlc.low.value
    if today_low is None and today_closed_m1:
        today_low = min(c.low for c in today_closed_m1)
    today_open = underlying.day_ohlc.open.value
    if today_open is None and today_closed_m1:
        today_open = today_closed_m1[0].open

    or_end = session_open + timedelta(minutes=or_minutes)
    or_candles = [c for c in today_closed_m1 if session_open <= c.start < or_end]
    opening_range_high = max((c.high for c in or_candles), default=None)
    opening_range_low = min((c.low for c in or_candles), default=None)

    prev_day = underlying.prev_day_ohlc
    prev_day_high = prev_day.high.value if prev_day else None
    prev_day_low = prev_day.low.value if prev_day else None
    prev_day_close = prev_day.close.value if prev_day else None

    prev_week = underlying.prev_week_ohlc
    prev_week_high = prev_week.high.value if prev_week else None
    prev_week_low = prev_week.low.value if prev_week else None
    prev_week_close = prev_week.close.value if prev_week else None

    current_week_high: float | None = None
    current_week_low: float | None = None
    # Only genuine multi-day D1 history may be aggregated into a "current
    # week" range. Today's own M1 candles must NEVER substitute for it -
    # aggregating a single day's bars as if they were a week's worth of
    # daily bars would relabel "today's range" as "this week's range",
    # which is fabrication, not an estimate. If no D1 history exists yet
    # (the common case for a single live snapshot - see module docstring),
    # this honestly stays None rather than faking a value.
    d1 = underlying.candles.get(Timeframe.D1, ())
    if d1:
        weekly = aggregate_weekly(d1, as_of=as_of, session_window=window)
        iso_year, iso_week, _ = today.isocalendar()
        for wk in weekly:
            wk_year, wk_week, _ = _to_ist_date(wk.start).isocalendar()
            if (wk_year, wk_week) == (iso_year, iso_week):
                current_week_high, current_week_low = wk.high, wk.low
                break

    midpoint = (today_high + today_low) / 2 if today_high is not None and today_low is not None else None

    return SessionLevels(
        prev_day_high=prev_day_high,
        prev_day_low=prev_day_low,
        prev_day_close=prev_day_close,
        today_open=today_open,
        today_high=today_high,
        today_low=today_low,
        session_midpoint=midpoint,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        prev_week_high=prev_week_high,
        prev_week_low=prev_week_low,
        prev_week_close=prev_week_close,
        current_week_high=current_week_high,
        current_week_low=current_week_low,
    )
