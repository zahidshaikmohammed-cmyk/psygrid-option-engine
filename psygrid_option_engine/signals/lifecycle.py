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
    by `f"{underlying}:{direction}:{framework}"` - callers should include a
    numeric identifying detail, such as the structural invalidation level,
    when the same framework/direction could otherwise recur as a distinct
    setup; see `run_engine.py::_lifecycle_key`).

    A resolved (terminal) setup identity does NOT silently resurrect on
    the next `update()` call just because the caller still sees tier-
    worthy evidence under the same key - that was the original behavior
    ("a concluded setup starts fresh") and it let an invalidated/targeted
    trade immediately re-register as active on the very next tick. The
    primary guard against that is identity: a genuinely new setup (a
    different structural level, different framework/direction) gets its
    own key automatically. `reentry_cooldown_seconds` is the secondary,
    explicit safeguard - even the exact same key may only leave a
    terminal state after this many seconds have passed since it entered
    one, guarding against a same-level whipsaw.
    """

    def __init__(self, *, reentry_cooldown_seconds: float = 0.0) -> None:
        self._tracked: dict[str, TrackedSetup] = {}
        self._reentry_cooldown_seconds = reentry_cooldown_seconds

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
            elapsed = (as_of - existing.last_seen).total_seconds()
            if elapsed >= self._reentry_cooldown_seconds:
                existing = None  # cooldown elapsed - this identity may start fresh again
            elif not (invalidated or targeted):
                # Still cooling down: stay terminal rather than resurrecting
                # mid-lifecycle. A fresh invalidated/targeted call is still
                # honored below (idempotent - it's already terminal).
                self._tracked[key] = TrackedSetup(
                    key=key, state=existing.state, tier=existing.tier,
                    first_seen=existing.first_seen, last_seen=existing.last_seen,
                )
                return existing.state

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

    def force_expire(self, key: str, *, as_of: datetime) -> LifecycleState:
        """Explicit terminal transition for a reason outside the normal
        evidence-driven lifecycle - specifically the 15:00 IST entry
        cutoff (brief section 6): this is a signal-only engine with no
        broker position to square off, but an internally ACTIVE setup
        must not appear to remain live past the point real intraday risk
        would need to be closed. Reuses the existing EXPIRED terminal
        state rather than inventing a parallel one."""
        existing = self._tracked.get(key)
        if existing is not None and existing.state in _TERMINAL_STATES:
            return existing.state
        first_seen = existing.first_seen if existing is not None else as_of
        tier = existing.tier if existing is not None else 0
        self._tracked[key] = TrackedSetup(
            key=key, state=LifecycleState.EXPIRED, tier=tier, first_seen=first_seen, last_seen=as_of
        )
        return LifecycleState.EXPIRED

    def get(self, key: str) -> TrackedSetup | None:
        return self._tracked.get(key)

    def is_cooling_down(self, key: str, *, as_of: datetime) -> bool:
        """True if this exact setup identity is currently in a terminal
        state and the reentry cooldown has not yet elapsed - callers that
        register/activate a setup (e.g. `run_engine.py`'s active-trade
        monitoring) should skip doing so while this is True, rather than
        only checking `get(key).state` directly, since a terminal
        `TrackedSetup` alone does not reflect whether the cooldown clock
        has actually run out."""
        existing = self._tracked.get(key)
        if existing is None or existing.state not in _TERMINAL_STATES:
            return False
        elapsed = (as_of - existing.last_seen).total_seconds()
        return elapsed < self._reentry_cooldown_seconds

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
