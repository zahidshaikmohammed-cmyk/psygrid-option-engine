"""Deterministic lower-to-higher timeframe candle aggregation.

Every aggregated candle is tagged `source="aggregated:<base>"` so it is
never mistaken for source data (docs/ARCHITECTURE.md section 3/17). Bucket
boundaries for intraday timeframes are aligned to the trading session's
open (default 09:15 IST), not to epoch/midnight — a 5-minute bucket is
[09:15,09:20), [09:20,09:25), ... not [09:00,09:05) etc. Daily/weekly
bars are aligned to session open/close via `SessionWindow`.

`is_closed` is always computed relative to the caller-supplied `as_of`
instant (the decision's information boundary), never `datetime.now()` -
this is what keeps replay and live paths byte-for-byte consistent.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from psygrid_option_engine.config.session import IST, SessionWindow
from psygrid_option_engine.domain.timeframe import (
    INTRADAY_TIMEFRAMES,
    Candle,
    Timeframe,
)


def _to_ist(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST)


def _sum_volume(candles: Sequence[Candle]) -> float | None:
    known = [c.volume for c in candles if c.volume is not None]
    return sum(known) if known else None


def _bucket_start(moment: datetime, target: Timeframe, session_open: time) -> datetime:
    ist_moment = _to_ist(moment)
    session_open_dt = datetime.combine(ist_moment.date(), session_open, tzinfo=IST)
    minutes_since_open = (ist_moment - session_open_dt).total_seconds() / 60
    bucket_index = math.floor(minutes_since_open / target.minutes)
    bucket_start_ist = session_open_dt + timedelta(minutes=bucket_index * target.minutes)
    return bucket_start_ist.astimezone(UTC)


def aggregate_intraday(
    base_candles: Sequence[Candle],
    target: Timeframe,
    *,
    as_of: datetime,
    session_open: time = time(9, 15),
) -> list[Candle]:
    """Aggregate same-timeframe base candles into `target` buckets.

    Silently drops any candle whose `start` is after `as_of` (defense in
    depth against accidental future-data leakage even if the caller
    already should have filtered). Raises if base candles mix timeframes
    or aren't strictly finer than `target`.
    """
    if target not in INTRADAY_TIMEFRAMES:
        raise ValueError(f"{target} is not an intraday timeframe")

    base_tf: Timeframe | None = None
    groups: dict[datetime, list[Candle]] = defaultdict(list)

    for c in base_candles:
        if c.start > as_of:
            continue
        if base_tf is None:
            base_tf = c.timeframe
        elif c.timeframe != base_tf:
            raise ValueError("aggregate_intraday requires a single source timeframe")
        if c.timeframe >= target:
            raise ValueError(f"base timeframe {c.timeframe} is not finer than target {target}")
        groups[_bucket_start(c.start, target, session_open)].append(c)

    result: list[Candle] = []
    for bucket_start, candles in sorted(groups.items()):
        candles.sort(key=lambda c: c.start)
        bucket_end = bucket_start + timedelta(minutes=target.minutes)
        result.append(
            Candle(
                timeframe=target,
                start=bucket_start,
                end=bucket_end,
                open=candles[0].open,
                high=max(c.high for c in candles),
                low=min(c.low for c in candles),
                close=candles[-1].close,
                volume=_sum_volume(candles),
                is_closed=bucket_end <= as_of and all(c.is_closed for c in candles),
                source=f"aggregated:{base_tf.name if base_tf else 'unknown'}",
            )
        )
    return result


def aggregate_daily(
    base_candles: Sequence[Candle],
    *,
    as_of: datetime,
    session_window: SessionWindow | None = None,
) -> list[Candle]:
    window = session_window or SessionWindow()
    base_tf: Timeframe | None = None
    groups: dict[date, list[Candle]] = defaultdict(list)

    for c in base_candles:
        if c.start > as_of:
            continue
        base_tf = base_tf or c.timeframe
        groups[_to_ist(c.start).date()].append(c)

    result: list[Candle] = []
    for day, candles in sorted(groups.items()):
        candles.sort(key=lambda c: c.start)
        day_open, day_close = window.session_bounds(day)
        result.append(
            Candle(
                timeframe=Timeframe.D1,
                start=day_open,
                end=day_close,
                open=candles[0].open,
                high=max(c.high for c in candles),
                low=min(c.low for c in candles),
                close=candles[-1].close,
                volume=_sum_volume(candles),
                is_closed=day_close <= as_of,
                source=f"aggregated:{base_tf.name if base_tf else 'unknown'}",
            )
        )
    return result


def aggregate_weekly(
    base_candles: Sequence[Candle],
    *,
    as_of: datetime,
    session_window: SessionWindow | None = None,
) -> list[Candle]:
    """Groups by ISO calendar week; week bounds are Monday's session open
    through Friday's session close, regardless of which days within the
    week actually had data (holidays leave gaps, not a shifted week)."""
    window = session_window or SessionWindow()
    base_tf: Timeframe | None = None
    groups: dict[tuple[int, int], list[Candle]] = defaultdict(list)

    for c in base_candles:
        if c.start > as_of:
            continue
        base_tf = base_tf or c.timeframe
        iso_year, iso_week, _ = _to_ist(c.start).date().isocalendar()
        groups[(iso_year, iso_week)].append(c)

    result: list[Candle] = []
    for (iso_year, iso_week), candles in sorted(groups.items()):
        candles.sort(key=lambda c: c.start)
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        friday = date.fromisocalendar(iso_year, iso_week, 5)
        week_open, _ = window.session_bounds(monday)
        _, week_close = window.session_bounds(friday)
        result.append(
            Candle(
                timeframe=Timeframe.W1,
                start=week_open,
                end=week_close,
                open=candles[0].open,
                high=max(c.high for c in candles),
                low=min(c.low for c in candles),
                close=candles[-1].close,
                volume=_sum_volume(candles),
                is_closed=week_close <= as_of,
                source=f"aggregated:{base_tf.name if base_tf else 'unknown'}",
            )
        )
    return result


def latest_closed(candles: Sequence[Candle]) -> Candle | None:
    """The most recent *closed* candle, or None. Callers computing
    indicators over "the last N bars" must go through this rather than
    naively taking `candles[-1]`, which may be a still-forming bar."""
    closed = [c for c in candles if c.is_closed]
    return max(closed, key=lambda c: c.start) if closed else None
