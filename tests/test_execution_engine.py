from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import OptionLeg
from psygrid_option_engine.execution.engine import engineer_trade

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def _leg(
    ltp: float = 100.0, delta: float | None = 0.5, bid: float | None = None, ask: float | None = None
) -> OptionLeg:
    return OptionLeg(
        security_id="A",
        symbol="NIFTY25000CE",
        strike=25000,
        option_type="CE",
        expiry=date(2026, 9, 25),
        ltp=_sf(ltp),
        bid=_sf(bid),
        ask=_sf(ask),
        volume=_sf(1000),
        oi=_sf(10000),
        oi_change=_sf(None),
        iv=_sf(None),
        delta=_sf(delta),
        gamma=_sf(None),
        theta=_sf(None),
        vega=_sf(None),
    )


def test_basic_trade_engineering() -> None:
    leg = _leg(ltp=100.0, delta=0.5)
    result = engineer_trade(
        leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=25100
    )
    assert result.plan is not None
    # underlying stop distance 50, target distance 100
    expected_stop_distance = 0.5 * 50 * 1.15
    expected_target_distance = 0.5 * 100
    assert result.plan.stop_loss == pytest.approx(100 - expected_stop_distance)
    assert result.plan.take_profit == pytest.approx(100 + expected_target_distance)
    assert result.plan.risk_reward == pytest.approx(expected_target_distance / expected_stop_distance)


def test_missing_delta_rejects() -> None:
    leg = _leg(delta=None)
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=25100)
    assert result.plan is None
    assert "delta" in result.rejection_reason


def test_missing_target_rejects() -> None:
    leg = _leg()
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=None)
    assert result.plan is None
    assert "target" in result.rejection_reason


def test_missing_underlying_price_rejects() -> None:
    leg = _leg()
    result = engineer_trade(leg, direction="CALL", underlying_ltp=None, invalidation_level=24950, target_level=25100)
    assert result.plan is None


def test_zero_delta_rejects() -> None:
    leg = _leg(delta=0.0)
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=25100)
    assert result.plan is None
    assert "zero" in result.rejection_reason


def test_falls_back_to_bid_ask_mid_when_no_ltp() -> None:
    leg = _leg(ltp=None, delta=0.5, bid=95, ask=105)
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=25100)
    assert result.plan is not None
    assert result.plan.entry == 100.0


def test_no_premium_price_at_all_rejects() -> None:
    leg = _leg(ltp=None, delta=0.5, bid=None, ask=None)
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24950, target_level=25100)
    assert result.plan is None
    assert "premium price" in result.rejection_reason


def test_wide_structural_stop_relative_to_premium_rejects() -> None:
    # underlying stop distance is huge relative to the cheap premium -> negative SL
    leg = _leg(ltp=10.0, delta=0.9)
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=24000, target_level=25500)
    assert result.plan is None
    assert "stop_loss" in result.rejection_reason


def test_put_direction_works_symmetrically() -> None:
    leg = _leg(ltp=100.0, delta=-0.5)
    result = engineer_trade(leg, direction="PUT", underlying_ltp=25000, invalidation_level=25050, target_level=24900)
    assert result.plan is not None
    # SL still below entry, TP still above entry (long-premium convention)
    assert result.plan.stop_loss < result.plan.entry < result.plan.take_profit


def test_invalidation_equal_to_price_rejects() -> None:
    leg = _leg()
    result = engineer_trade(leg, direction="CALL", underlying_ltp=25000, invalidation_level=25000, target_level=25100)
    assert result.plan is None
    assert "coincides" in result.rejection_reason


def test_custom_safety_margin_applied() -> None:
    leg = _leg(ltp=100.0, delta=0.5)
    result = engineer_trade(
        leg,
        direction="CALL",
        underlying_ltp=25000,
        invalidation_level=24950,
        target_level=25100,
        safety_margin=1.5,
    )
    assert result.plan is not None
    assert result.plan.stop_loss == pytest.approx(100 - 0.5 * 50 * 1.5)
