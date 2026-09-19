"""Signal lifecycle state machine (brief section 27).

Used only in `--live` mode to avoid duplicate-signal spam across
consecutive ticks that keep finding the same developing/active setup.
Purely in-memory and per-process - it is not part of the deterministic
decision core (`api/decision.py`), which stays stateless and reproducible
from a single snapshot; this tracker only decides what's *worth printing
again*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LifecycleState(StrEnum):
    SCANNING = "SCANNING"
    SETUP_FORMING = "SETUP_FORMING"
    WATCH = "WATCH"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    TARGET = "TARGET"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


_TERMINAL_STATES = frozenset({LifecycleState.TARGET, LifecycleState.INVALIDATED, LifecycleState.EXPIRED})
_PRE_TRIGGER_STATES = frozenset({LifecycleState.SCANNING, LifecycleState.SETUP_FORMING, LifecycleState.WATCH})
_POST_TRIGGER_STATES = frozenset({LifecycleState.TRIGGERED, LifecycleState.CONFIRMED, LifecycleState.ACTIVE})


@dataclass(frozen=True)
class TrackedSetup:
    key: str
    state: LifecycleState
    tier: int
    first_seen: datetime
    last_seen: datetime


class LifecycleTracker:
    """In-memory tracker, one instance per underlying (or per process, keyed
    by `f"{underlying}:{direction}:{framework}"`)."""

    def __init__(self) -> None:
        self._tracked: dict[str, TrackedSetup] = {}

    def update(
        self,
        *,
        key: str,
        tier: int,
        as_of: datetime,
        invalidated: bool = False,
        targeted: bool = False,
    ) -> LifecycleState:
        existing = self._tracked.get(key)
        if existing is not None and existing.state in _TERMINAL_STATES:
            existing = None  # a concluded setup starts fresh rather than resurrecting a terminal state

        if invalidated:
            new_state = LifecycleState.INVALIDATED
        elif targeted:
            new_state = LifecycleState.TARGET
        elif tier <= 0:
            new_state = LifecycleState.SETUP_FORMING
        elif tier == 1:
            new_state = LifecycleState.WATCH
        elif existing is None or existing.state in _PRE_TRIGGER_STATES:
            new_state = LifecycleState.TRIGGERED
        elif existing.state is LifecycleState.TRIGGERED:
            new_state = LifecycleState.CONFIRMED
        else:
            new_state = LifecycleState.ACTIVE

        first_seen = existing.first_seen if existing is not None else as_of
        self._tracked[key] = TrackedSetup(key=key, state=new_state, tier=tier, first_seen=first_seen, last_seen=as_of)
        return new_state

    def get(self, key: str) -> TrackedSetup | None:
        return self._tracked.get(key)

    def is_repeat(self, key: str, tier: int) -> bool:
        """True if this (key, tier) was already reported in an
        already-confirmed/active state - callers use this to suppress a
        duplicate print on an unchanged setup."""
        existing = self._tracked.get(key)
        return existing is not None and existing.tier == tier and existing.state in _POST_TRIGGER_STATES

    def expire_stale(self, *, as_of: datetime, max_age_seconds: float) -> list[str]:
        expired: list[str] = []
        for key, tracked in list(self._tracked.items()):
            if tracked.state in _TERMINAL_STATES:
                continue
            if (as_of - tracked.last_seen).total_seconds() > max_age_seconds:
                self._tracked[key] = TrackedSetup(
                    key=key, state=LifecycleState.EXPIRED, tier=tracked.tier,
                    first_seen=tracked.first_seen, last_seen=tracked.last_seen,
                )
                expired.append(key)
        return expired

    def reset(self, key: str) -> None:
        self._tracked.pop(key, None)
