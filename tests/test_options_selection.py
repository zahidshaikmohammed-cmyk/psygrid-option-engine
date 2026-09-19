from __future__ import annotations

from datetime import UTC, date, datetime

from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import (
    DepthLevel,
    DepthSnapshot,
    InstrumentDepth,
    OptionChainSnapshot,
    OptionLeg,
)
from psygrid_option_engine.options.selection import select_contract

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)
EXPIRY = date(2026, 9, 25)
LATER_EXPIRY = date(2026, 10, 2)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def _leg(
    strike: float,
    option_type: str,
    *,
    security_id: str = "X",
    expiry: date = EXPIRY,
    bid: float | None = 99,
    ask: float | None = 101,
    oi: float | None = 10000,
    volume: float | None = 5000,
    delta: float | None = 0.5,
) -> OptionLeg:
    return OptionLeg(
        security_id=security_id,
        symbol=f"NIFTY{strike}{option_type}",
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        expiry=expiry,
        ltp=_sf(100),
        bid=_sf(bid),
        ask=_sf(ask),
        volume=_sf(volume),
        oi=_sf(oi),
        oi_change=_sf(None),
        iv=_sf(None),
        delta=_sf(delta),
        gamma=_sf(None),
        theta=_sf(None),
        vega=_sf(None),
    )


def test_no_chain_returns_no_selection() -> None:
    result = select_contract(None, direction="CALL", underlying_ltp=25000)
    assert result.selected is None
    assert result.candidates == ()


def test_selects_survivor_when_only_one_meets_constraints() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(25000, "CE", security_id="A"),))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000)
    assert result.selected is not None
    assert result.selected.leg.security_id == "A"


def test_rejects_low_oi() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(25000, "CE", security_id="A", oi=10),))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, min_oi=500)
    assert result.selected is None
    assert result.candidates[0].rejected is True
    assert "open interest" in result.candidates[0].rejection_reason


def test_rejects_wide_spread() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY", legs=(_leg(25000, "CE", security_id="A", bid=50, ask=150),)
    )
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, max_spread_pct=5.0)
    assert result.selected is None
    assert "spread" in result.candidates[0].rejection_reason


def test_rejects_delta_outside_band() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(25000, "CE", security_id="A", delta=0.05),))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, target_delta=0.5, delta_band=0.15)
    assert result.selected is None
    assert "delta" in result.candidates[0].rejection_reason


def test_prefers_better_liquidity_and_tighter_spread_over_high_oi_alone() -> None:
    # Section 10: must NOT simply always select highest OI/nearest strike.
    # A is highest OI but very wide spread; B has decent OI and tight spread.
    a = _leg(24900, "CE", security_id="A", oi=100000, bid=90, ask=110)  # ~20% spread -> hard rejected
    b = _leg(25100, "CE", security_id="B", oi=8000, bid=99.5, ask=100.5)  # tight spread
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(a, b))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, max_spread_pct=5.0)
    assert result.selected is not None
    assert result.selected.leg.security_id == "B"


def test_does_not_always_pick_nearest_strike_when_liquidity_favors_other() -> None:
    near = _leg(25000, "CE", security_id="NEAR", oi=600, volume=100, bid=99, ask=101)
    far = _leg(25200, "CE", security_id="FAR", oi=50000, volume=20000, bid=99.8, ask=100.2)
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(near, far))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000)
    assert result.selected is not None
    assert result.selected.leg.security_id == "FAR"


def test_defaults_to_nearest_expiry() -> None:
    near = _leg(25000, "CE", security_id="NEAR_EXP", expiry=EXPIRY)
    far = _leg(25000, "CE", security_id="FAR_EXP", expiry=LATER_EXPIRY)
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(near, far))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000)
    assert result.selected is not None
    assert result.selected.leg.security_id == "NEAR_EXP"


def test_explicit_expiry_respected() -> None:
    near = _leg(25000, "CE", security_id="NEAR_EXP", expiry=EXPIRY)
    far = _leg(25000, "CE", security_id="FAR_EXP", expiry=LATER_EXPIRY)
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(near, far))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, expiry=LATER_EXPIRY)
    assert result.selected is not None
    assert result.selected.leg.security_id == "FAR_EXP"


def test_put_selection_uses_pe_legs() -> None:
    ce = _leg(25000, "CE", security_id="CE1")
    pe = _leg(25000, "PE", security_id="PE1", delta=-0.5)
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(ce, pe))
    result = select_contract(chain, direction="PUT", underlying_ltp=25000)
    assert result.selected is not None
    assert result.selected.leg.security_id == "PE1"


def test_uses_real_depth_over_bid_ask_fallback_when_available() -> None:
    leg = _leg(25000, "CE", security_id="A", bid=90, ask=110)  # wide bid/ask would reject via fallback
    depth = DepthSnapshot(
        underlying="NIFTY",
        by_security_id={
            "A": InstrumentDepth(
                security_id="A",
                bids=(DepthLevel(price=99.5, quantity=500),),
                asks=(DepthLevel(price=100.5, quantity=500),),
            )
        },
    )
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(leg,))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000, max_spread_pct=5.0, depth=depth)
    assert result.selected is not None  # real depth (tight) used instead of wide bid/ask


def test_missing_security_id_rejected() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(25000, "CE", security_id=""),))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000)
    assert result.selected is None
    assert "security_id" in result.candidates[0].rejection_reason


def test_all_evaluated_candidates_kept_for_audit_trail() -> None:
    good = _leg(25000, "CE", security_id="GOOD")
    bad = _leg(25100, "CE", security_id="BAD", oi=1)
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(good, bad))
    result = select_contract(chain, direction="CALL", underlying_ltp=25000)
    assert len(result.candidates) == 2
    assert any(c.rejected for c in result.candidates)
    assert any(not c.rejected for c in result.candidates)
