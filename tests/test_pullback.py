from __future__ import annotations

import pytest

from psygrid_option_engine.market.pullback import PullbackType, classify_pullback


def test_shallow_pullback() -> None:
    # impulse 100 -> 200, current price retraced to 180 (20% retracement)
    result = classify_pullback(impulse_start=100, impulse_end=200, current_price=180)
    assert result.kind is PullbackType.SHALLOW
    assert result.retracement_pct == pytest.approx(20.0)


def test_moderate_pullback() -> None:
    result = classify_pullback(impulse_start=100, impulse_end=200, current_price=150)
    assert result.kind is PullbackType.MODERATE
    assert result.retracement_pct == pytest.approx(50.0)


def test_deep_pullback() -> None:
    result = classify_pullback(impulse_start=100, impulse_end=200, current_price=120)
    assert result.kind is PullbackType.DEEP
    assert result.retracement_pct == pytest.approx(80.0)


def test_failed_pullback_exceeds_full_retracement() -> None:
    result = classify_pullback(impulse_start=100, impulse_end=200, current_price=95)
    assert result.kind is PullbackType.FAILED


def test_reversal_structure_takes_priority() -> None:
    result = classify_pullback(
        impulse_start=100,
        impulse_end=200,
        current_price=150,
        made_new_extreme_beyond_start=True,
    )
    assert result.kind is PullbackType.REVERSAL_STRUCTURE


def test_continuation_overrides_depth() -> None:
    result = classify_pullback(
        impulse_start=100,
        impulse_end=200,
        current_price=180,  # shallow retracement depth
        resumed_beyond_impulse_end=True,
    )
    assert result.kind is PullbackType.CONTINUATION


def test_zero_span_is_none() -> None:
    result = classify_pullback(impulse_start=100, impulse_end=100, current_price=100)
    assert result.kind is PullbackType.NONE
    assert result.retracement_pct is None


def test_legs_reported_independently() -> None:
    result = classify_pullback(
        impulse_start=100, impulse_end=200, current_price=180, post_impulse_swing_count=2
    )
    assert result.legs == 2
