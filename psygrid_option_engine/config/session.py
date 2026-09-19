"""Indian equity-derivatives session-window rules (section 16 of the brief).

All comparisons are done in IST (`Asia/Kolkata`) regardless of the host
machine's local timezone. Callers pass timezone-aware `datetime` objects (or
naive ones, which are assumed UTC and converted) — see `SessionWindow.phase_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class SessionPhase(StrEnum):
    """Where a given instant falls relative to the trading day."""

    PRE_MARKET = "PRE_MARKET"
    OPENING = "OPENING"
    NORMAL = "NORMAL"
    LATE_SESSION = "LATE_SESSION"
    POST_ENTRY_CUTOFF = "POST_ENTRY_CUTOFF"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class SessionWindow:
    """Configurable session-time boundaries, all in IST wall-clock time.

    Defaults match section 16 of the brief:
        09:15 -> market open
        15:30 -> market close
        15:00 -> intraday positions should be considered closed
    `opening_period_minutes` and `entry_cutoff` are separate knobs so the
    "opening period" (structurally noisy, often excluded from structure
    signals) and the "no new entries after this time" cutoff can be tuned
    independently without touching engine logic.
    """

    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    opening_period_minutes: int = 15
    entry_cutoff: time = time(15, 0)
    late_session_start: time = time(14, 30)

    def phase_at(self, moment: datetime) -> SessionPhase:
        ist_moment = _to_ist(moment)
        t = ist_moment.time()

        if t < self.market_open or t >= self.market_close:
            return SessionPhase.PRE_MARKET if t < self.market_open else SessionPhase.CLOSED

        opening_end = _add_minutes(self.market_open, self.opening_period_minutes)
        if t < opening_end:
            return SessionPhase.OPENING
        if t >= self.entry_cutoff:
            return SessionPhase.POST_ENTRY_CUTOFF
        if t >= self.late_session_start:
            return SessionPhase.LATE_SESSION
        return SessionPhase.NORMAL

    def is_new_entry_allowed(self, moment: datetime) -> bool:
        """False once past entry_cutoff, before open, or after close."""
        return self.phase_at(moment) in (
            SessionPhase.OPENING,
            SessionPhase.NORMAL,
            SessionPhase.LATE_SESSION,
        )

    def is_within_session(self, moment: datetime) -> bool:
        return self.phase_at(moment) not in (SessionPhase.PRE_MARKET, SessionPhase.CLOSED)

    def session_bounds(self, day: date) -> tuple[datetime, datetime]:
        """UTC (open, close) instants for the given IST calendar date."""
        open_dt = datetime.combine(day, self.market_open, tzinfo=IST)
        close_dt = datetime.combine(day, self.market_close, tzinfo=IST)
        return open_dt.astimezone(UTC), close_dt.astimezone(UTC)


def _to_ist(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST)


def _add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    return time(hour=(total // 60) % 24, minute=total % 60)
