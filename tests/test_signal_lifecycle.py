from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.signals.lifecycle import LifecycleState, LifecycleTracker

T0 = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def test_tier_zero_is_setup_forming() -> None:
    tracker = LifecycleTracker()
    state = tracker.update(key="NIFTY:CALL:TREND", tier=0, as_of=T0)
    assert state is LifecycleState.SETUP_FORMING


def test_tier_one_is_watch() -> None:
    tracker = LifecycleTracker()
    state = tracker.update(key="k", tier=1, as_of=T0)
    assert state is LifecycleState.WATCH


def test_progression_triggered_then_confirmed_then_active() -> None:
    tracker = LifecycleTracker()
    key = "k"
    s1 = tracker.update(key=key, tier=2, as_of=T0)
    s2 = tracker.update(key=key, tier=2, as_of=T0 + timedelta(minutes=1))
    s3 = tracker.update(key=key, tier=2, as_of=T0 + timedelta(minutes=2))
    s4 = tracker.update(key=key, tier=2, as_of=T0 + timedelta(minutes=3))
    assert s1 is LifecycleState.TRIGGERED
    assert s2 is LifecycleState.CONFIRMED
    assert s3 is LifecycleState.ACTIVE
    assert s4 is LifecycleState.ACTIVE


def test_invalidated_overrides_tier() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    state = tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    assert state is LifecycleState.INVALIDATED


def test_target_overrides_tier() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    state = tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), targeted=True)
    assert state is LifecycleState.TARGET


def test_terminal_state_resets_after_cooldown_elapses() -> None:
    # Default reentry_cooldown_seconds=0.0 (no cooldown configured) - the
    # identity may start fresh again as soon as any time has passed.
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    state = tracker.update(key=key, tier=2, as_of=T0 + timedelta(minutes=5))
    assert state is LifecycleState.TRIGGERED


def test_terminal_state_does_not_resurrect_immediately_with_cooldown() -> None:
    # Production hardening requirement (section 7): a resolved setup must
    # not immediately resurrect. With an explicit cooldown configured, the
    # very next tick after invalidation must NOT re-trigger even though
    # the caller still reports tier-worthy evidence under the same key.
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    state = tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1, seconds=15))
    assert state is LifecycleState.INVALIDATED  # still cooling down, not resurrected
    assert tracker.get(key).state is LifecycleState.INVALIDATED


def test_terminal_state_resurrects_once_cooldown_elapses() -> None:
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    state = tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=6, seconds=1))
    assert state is LifecycleState.TRIGGERED


def test_different_structural_level_is_a_different_identity_not_gated_by_cooldown() -> None:
    # A genuinely new setup (a different key - e.g. a new structural
    # invalidation level) must never be blocked by another key's cooldown.
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    tracker.update(key="NIFTY:CALL:TREND:24400", tier=3, as_of=T0)
    tracker.update(key="NIFTY:CALL:TREND:24400", tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    state = tracker.update(key="NIFTY:CALL:TREND:24550", tier=2, as_of=T0 + timedelta(minutes=1, seconds=5))
    assert state is LifecycleState.TRIGGERED


def test_force_expire_sets_expired_state() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    state = tracker.force_expire(key, as_of=T0 + timedelta(minutes=1))
    assert state is LifecycleState.EXPIRED
    assert tracker.get(key).state is LifecycleState.EXPIRED


def test_force_expire_on_unknown_key_still_creates_expired_entry() -> None:
    tracker = LifecycleTracker()
    state = tracker.force_expire("never-seen", as_of=T0)
    assert state is LifecycleState.EXPIRED
    assert tracker.get("never-seen").state is LifecycleState.EXPIRED


def test_force_expire_does_not_override_an_existing_terminal_state() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), targeted=True)
    tracker.force_expire(key, as_of=T0 + timedelta(minutes=2))
    # already resolved via TARGET - force_expire must not relabel it EXPIRED
    assert tracker.get(key).state is LifecycleState.TARGET


def test_is_cooling_down_true_immediately_after_terminal_state() -> None:
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    assert tracker.is_cooling_down(key, as_of=T0 + timedelta(minutes=1, seconds=1)) is True


def test_is_cooling_down_false_after_cooldown_elapses() -> None:
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    key = "k"
    tracker.update(key=key, tier=3, as_of=T0)
    tracker.update(key=key, tier=3, as_of=T0 + timedelta(minutes=1), invalidated=True)
    assert tracker.is_cooling_down(key, as_of=T0 + timedelta(minutes=6, seconds=1)) is False


def test_is_cooling_down_false_for_unknown_key() -> None:
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    assert tracker.is_cooling_down("never-seen", as_of=T0) is False


def test_is_cooling_down_false_for_non_terminal_state() -> None:
    tracker = LifecycleTracker(reentry_cooldown_seconds=300.0)
    key = "k"
    tracker.update(key=key, tier=2, as_of=T0)  # TRIGGERED, not terminal
    assert tracker.is_cooling_down(key, as_of=T0) is False


def test_is_repeat_true_for_unchanged_active_setup() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=2, as_of=T0)
    tracker.update(key=key, tier=2, as_of=T0 + timedelta(minutes=1))
    assert tracker.is_repeat(key, 2) is True


def test_is_repeat_false_for_new_setup() -> None:
    tracker = LifecycleTracker()
    assert tracker.is_repeat("unknown", 2) is False


def test_is_repeat_false_while_still_watching() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=1, as_of=T0)
    assert tracker.is_repeat(key, 1) is False  # WATCH is pre-trigger, not a repeat yet


def test_expire_stale_marks_old_entries_expired() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=1, as_of=T0)
    expired = tracker.expire_stale(as_of=T0 + timedelta(hours=1), max_age_seconds=300)
    assert key in expired
    assert tracker.get(key).state is LifecycleState.EXPIRED


def test_expire_stale_ignores_recent_entries() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=1, as_of=T0)
    expired = tracker.expire_stale(as_of=T0 + timedelta(seconds=10), max_age_seconds=300)
    assert expired == []


def test_reset_clears_tracked_key() -> None:
    tracker = LifecycleTracker()
    key = "k"
    tracker.update(key=key, tier=1, as_of=T0)
    tracker.reset(key)
    assert tracker.get(key) is None


def test_independent_keys_do_not_interfere() -> None:
    tracker = LifecycleTracker()
    tracker.update(key="NIFTY:CALL:TREND", tier=2, as_of=T0)
    state = tracker.update(key="NIFTY:PUT:SWEEP", tier=0, as_of=T0)
    assert state is LifecycleState.SETUP_FORMING
    assert tracker.get("NIFTY:CALL:TREND").state is LifecycleState.TRIGGERED
