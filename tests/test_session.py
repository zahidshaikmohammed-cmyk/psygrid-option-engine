from __future__ import annotations

from datetime import UTC, datetime

import pytest

from psygrid_option_engine.config.session import SessionPhase, SessionWindow

WINDOW = SessionWindow()


def _ist(hour: int, minute: int) -> datetime:
    # Build via UTC offset for IST (+5:30) without depending on zoneinfo data
    # for the test's own construction, independent of the code under test.
    from datetime import timedelta

    naive_ist = datetime(2026, 9, 18, hour, minute)
    return (naive_ist - timedelta(hours=5, minutes=30)).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 0, SessionPhase.PRE_MARKET),
        (9, 14, SessionPhase.PRE_MARKET),
        (9, 15, SessionPhase.OPENING),
        (9, 29, SessionPhase.OPENING),
        (9, 30, SessionPhase.NORMAL),
        (12, 0, SessionPhase.NORMAL),
        (14, 29, SessionPhase.NORMAL),
        (14, 30, SessionPhase.LATE_SESSION),
        (14, 59, SessionPhase.LATE_SESSION),
        (15, 0, SessionPhase.POST_ENTRY_CUTOFF),
        (15, 29, SessionPhase.POST_ENTRY_CUTOFF),
        (15, 30, SessionPhase.CLOSED),
        (20, 0, SessionPhase.CLOSED),
    ],
)
def test_phase_at_boundaries(hour: int, minute: int, expected: SessionPhase) -> None:
    assert WINDOW.phase_at(_ist(hour, minute)) is expected


def test_naive_datetime_assumed_utc() -> None:
    # 10:00 UTC == 15:30 IST == CLOSED under default window.
    naive = datetime(2026, 9, 18, 10, 0)
    assert WINDOW.phase_at(naive) is SessionPhase.CLOSED


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 15, True),
        (14, 59, True),
        (15, 0, False),
        (15, 29, False),
        (9, 0, False),
        (15, 30, False),
    ],
)
def test_is_new_entry_allowed(hour: int, minute: int, expected: bool) -> None:
    assert WINDOW.is_new_entry_allowed(_ist(hour, minute)) is expected


def test_is_within_session() -> None:
    assert WINDOW.is_within_session(_ist(10, 0)) is True
    assert WINDOW.is_within_session(_ist(8, 0)) is False
    assert WINDOW.is_within_session(_ist(16, 0)) is False


def test_session_bounds_round_trip() -> None:
    from datetime import date

    open_utc, close_utc = WINDOW.session_bounds(date(2026, 9, 18))
    assert WINDOW.phase_at(open_utc) is SessionPhase.OPENING
    # market_close boundary itself is CLOSED (exclusive upper bound).
    assert WINDOW.phase_at(close_utc) is SessionPhase.CLOSED


def test_custom_window_respected() -> None:
    from datetime import time

    custom = SessionWindow(
        market_open=time(9, 0),
        market_close=time(16, 0),
        opening_period_minutes=5,
        entry_cutoff=time(15, 45),
        late_session_start=time(15, 0),
    )
    assert custom.phase_at(_ist(9, 4)) is SessionPhase.OPENING
    assert custom.phase_at(_ist(15, 50)) is SessionPhase.POST_ENTRY_CUTOFF
