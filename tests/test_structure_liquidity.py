from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from psygrid_option_engine.domain.timeframe import Candle, Timeframe
from psygrid_option_engine.structure.liquidity import (
    classify_reaction,
    classify_reactions_at_zones,
    identify_liquidity_zones,
)
from psygrid_option_engine.structure.types import (
    LabeledSwing,
    LiquidityKind,
    ReactionKind,
    SessionLevels,
    StructureLabel,
    SwingKind,
    SwingPoint,
)

START = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _candle(i: int, h: float, low: float, c: float) -> Candle:
    start = START + timedelta(minutes=i)
    return Candle(
        timeframe=Timeframe.M1,
        start=start,
        end=start + timedelta(minutes=1),
        open=c,
        high=h,
        low=low,
        close=c,
        volume=100,
        is_closed=True,
        source="raw",
    )


def test_identify_liquidity_zones_basic_levels() -> None:
    levels = SessionLevels(prev_day_high=100, prev_day_low=90, today_high=105, today_low=88)
    zones = identify_liquidity_zones(levels, swings=[])
    kinds = {z.kind for z in zones}
    assert LiquidityKind.PDH in kinds
    assert LiquidityKind.PDL in kinds
    assert LiquidityKind.SESSION_HIGH in kinds
    assert LiquidityKind.SESSION_LOW in kinds


def test_equal_highs_detected() -> None:
    def swing(price: float, idx: int) -> LabeledSwing:
        sp = SwingPoint(kind=SwingKind.HIGH, index=idx, time=START, price=price, confirmed_at=START)
        return LabeledSwing(swing=sp, label=StructureLabel.HH)

    swings = [swing(100.0, 0), swing(100.02, 1), swing(150.0, 2)]
    zones = identify_liquidity_zones(SessionLevels(), swings)
    equal_zones = [z for z in zones if z.kind is LiquidityKind.EQUAL_HIGH]
    assert len(equal_zones) == 1
    assert equal_zones[0].level == pytest.approx(100.01, abs=0.01)


def test_classify_reaction_acceptance_above() -> None:
    candles = [_candle(0, 105, 101, 104), _candle(1, 106, 102, 105)]
    assert classify_reaction(candles, level=100, direction="ABOVE") == ReactionKind.ACCEPTANCE


def test_classify_reaction_sweep_above() -> None:
    candles = [_candle(0, 108, 95, 98)]  # pierced well above 100 but closed back below
    assert classify_reaction(candles, level=100, direction="ABOVE") == ReactionKind.SWEEP


def test_classify_reaction_rejection_above() -> None:
    candles = [_candle(0, 100.05, 95, 98)]  # barely pierced, closed back below
    assert classify_reaction(candles, level=100, direction="ABOVE") == ReactionKind.REJECTION


def test_classify_reaction_failed_break_above() -> None:
    candles = [_candle(0, 105, 101, 103), _candle(1, 104, 96, 97)]  # closed above then back below
    assert classify_reaction(candles, level=100, direction="ABOVE") == ReactionKind.FAILED_BREAK


def test_classify_reaction_untested() -> None:
    candles = [_candle(0, 90, 80, 85)]
    assert classify_reaction(candles, level=100, direction="ABOVE") == ReactionKind.UNTESTED


def test_classify_reaction_acceptance_below() -> None:
    candles = [_candle(0, 99, 95, 96), _candle(1, 98, 94, 95)]
    assert classify_reaction(candles, level=100, direction="BELOW") == ReactionKind.ACCEPTANCE


def test_classify_reactions_at_zones_picks_correct_direction() -> None:
    # current price 100; a zone below (support) and a zone above (resistance)
    from psygrid_option_engine.structure.types import LiquidityZone

    support = LiquidityZone(kind=LiquidityKind.PDL, level=90, note="support")
    resistance = LiquidityZone(kind=LiquidityKind.PDH, level=110, note="resistance")
    # price stays well clear of both -> both UNTESTED
    candles = [_candle(0, 101, 99, 100)]
    reactions = classify_reactions_at_zones([support, resistance], candles, current_price=100)
    assert all(r.reaction == ReactionKind.UNTESTED for r in reactions)
