from __future__ import annotations

from datetime import UTC, datetime

import pytest

from psygrid_option_engine.config.session import SessionWindow
from psygrid_option_engine.market.volatility import compute_range_model
from psygrid_option_engine.structure.types import SessionLevels

# Session halfway point: 09:15 IST -> 15:30 IST is 06:52:30 UTC (03:45 + 3h07m30s)
HALFWAY_UTC = datetime(2026, 9, 18, 6, 52, 30, tzinfo=UTC)
WINDOW = SessionWindow()


def test_expected_range_blends_atr_and_vix() -> None:
    levels = SessionLevels(today_high=25050, today_low=24950)
    model = compute_range_model(
        levels=levels, atr_daily=100.0, vix=None, ltp=25000.0, as_of=HALFWAY_UTC, session_window=WINDOW
    )
    assert model.expected_daily_range == pytest.approx(100.0)
    assert model.time_remaining_minutes == pytest.approx(187.5, abs=0.01)
    assert model.expected_remaining_range == pytest.approx(100.0 * (0.5**0.5), rel=1e-6)
    assert model.upper_boundary == pytest.approx(25000 + model.expected_remaining_range / 2)
    assert model.lower_boundary == pytest.approx(25000 - model.expected_remaining_range / 2)
    assert model.range_utilization_pct == pytest.approx(100.0)


def test_vix_implied_move_computed_when_no_atr() -> None:
    model = compute_range_model(
        levels=SessionLevels(),
        atr_daily=None,
        vix=15.0,
        ltp=25000.0,
        as_of=HALFWAY_UTC,
        session_window=WINDOW,
    )
    expected_vix_move = 25000.0 * 0.15 / (252**0.5)
    assert model.vix_implied_daily_move == pytest.approx(expected_vix_move)
    assert model.expected_daily_range == pytest.approx(expected_vix_move)


def test_missing_everything_stays_none_not_fabricated() -> None:
    model = compute_range_model(
        levels=SessionLevels(), atr_daily=None, vix=None, ltp=None, as_of=HALFWAY_UTC, session_window=WINDOW
    )
    assert model.expected_daily_range is None
    assert model.expected_remaining_range is None
    assert model.upper_boundary is None
    assert model.lower_boundary is None


def test_no_time_remaining_after_close() -> None:
    close = datetime(2026, 9, 18, 11, 0, tzinfo=UTC)  # well past 15:30 IST
    model = compute_range_model(
        levels=SessionLevels(), atr_daily=100.0, vix=None, ltp=25000.0, as_of=close, session_window=WINDOW
    )
    assert model.time_remaining_minutes == 0
    assert model.expected_remaining_range == pytest.approx(0.0)
