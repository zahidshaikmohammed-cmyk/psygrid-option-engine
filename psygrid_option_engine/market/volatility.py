"""Expected-range / volatility model (brief section 12).

Explicitly an estimate, not a promise: blends the underlying's own ATR
with an India VIX-implied daily move (when available) and scales the
remaining-session portion by the square root of the remaining time
fraction (standard volatility time-scaling), never presented as a
calibrated probability (brief section 33).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psygrid_option_engine.config.session import IST, SessionWindow
from psygrid_option_engine.structure.types import SessionLevels

_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class RangeModel:
    atr_daily: float | None
    vix_implied_daily_move: float | None
    session_range_so_far: float | None
    expected_daily_range: float | None
    expected_remaining_range: float | None
    upper_boundary: float | None
    lower_boundary: float | None
    range_utilization_pct: float | None
    time_remaining_minutes: float | None


def compute_range_model(
    *,
    levels: SessionLevels,
    atr_daily: float | None,
    vix: float | None,
    ltp: float | None,
    as_of: datetime,
    session_window: SessionWindow | None = None,
) -> RangeModel:
    window = session_window or SessionWindow()

    session_range_so_far = None
    if levels.today_high is not None and levels.today_low is not None:
        session_range_so_far = levels.today_high - levels.today_low

    vix_daily_move: float | None = None
    if vix is not None and ltp is not None:
        vix_daily_move = ltp * (vix / 100.0) / (_TRADING_DAYS_PER_YEAR**0.5)

    candidates = [v for v in (atr_daily, vix_daily_move) if v is not None]
    expected_daily_range = (sum(candidates) / len(candidates)) if candidates else None

    open_dt, close_dt = _session_bounds_for(window, as_of)
    total_minutes = (close_dt - open_dt).total_seconds() / 60
    remaining_minutes = max(0.0, min(total_minutes, (close_dt - as_of).total_seconds() / 60))
    time_fraction_remaining = (remaining_minutes / total_minutes) if total_minutes > 0 else None

    expected_remaining_range = None
    if expected_daily_range is not None and time_fraction_remaining is not None:
        expected_remaining_range = expected_daily_range * (time_fraction_remaining**0.5)

    upper_boundary = None
    lower_boundary = None
    if ltp is not None and expected_remaining_range is not None:
        upper_boundary = ltp + expected_remaining_range / 2
        lower_boundary = ltp - expected_remaining_range / 2

    range_utilization_pct = None
    if session_range_so_far is not None and expected_daily_range not in (None, 0):
        range_utilization_pct = (session_range_so_far / expected_daily_range) * 100

    return RangeModel(
        atr_daily=atr_daily,
        vix_implied_daily_move=vix_daily_move,
        session_range_so_far=session_range_so_far,
        expected_daily_range=expected_daily_range,
        expected_remaining_range=expected_remaining_range,
        upper_boundary=upper_boundary,
        lower_boundary=lower_boundary,
        range_utilization_pct=range_utilization_pct,
        time_remaining_minutes=remaining_minutes,
    )


def _session_bounds_for(window: SessionWindow, as_of: datetime) -> tuple[datetime, datetime]:
    ist_date = as_of.astimezone(IST).date()
    return window.session_bounds(ist_date)
