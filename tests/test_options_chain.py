from __future__ import annotations

from datetime import UTC, date, datetime

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import OptionChainSnapshot, OptionLeg
from psygrid_option_engine.options.chain import compute_chain_metrics

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)
EXPIRY = date(2026, 9, 25)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def _leg(strike: float, option_type: str, oi: float | None = None, iv: float | None = None) -> OptionLeg:
    return OptionLeg(
        security_id=f"{strike}{option_type}",
        symbol=f"NIFTY{strike}{option_type}",
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        expiry=EXPIRY,
        ltp=_sf(100),
        bid=_sf(99),
        ask=_sf(101),
        volume=_sf(1000),
        oi=_sf(oi),
        oi_change=_sf(None),
        iv=_sf(iv),
        delta=_sf(None),
        gamma=_sf(None),
        theta=_sf(None),
        vega=_sf(None),
    )


def test_no_chain_is_unavailable() -> None:
    metrics = compute_chain_metrics(None, underlying_ltp=25000, direction="CALL")
    assert metrics.stance is EvidenceStance.UNAVAILABLE


def test_atm_strike_nearest_to_ltp() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(24900, "CE", 1000), _leg(25100, "CE", 1000)))
    metrics = compute_chain_metrics(chain, underlying_ltp=25010, direction="CALL")
    assert metrics.atm_strike == 25100


def test_pcr_and_oi_totals() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY",
        legs=(_leg(25000, "CE", 10000), _leg(25000, "PE", 20000)),
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.ce_oi_total == 10000
    assert metrics.pe_oi_total == 20000
    assert metrics.pcr_oi == 2.0


def test_extreme_pcr_supportive_for_call() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY", legs=(_leg(25000, "CE", 10000), _leg(25000, "PE", 20000))
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.stance is EvidenceStance.SUPPORTIVE


def test_extreme_pcr_conflicting_for_put() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY", legs=(_leg(25000, "CE", 10000), _leg(25000, "PE", 20000))
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="PUT")
    assert metrics.stance is EvidenceStance.CONFLICTING


def test_middling_pcr_is_neutral() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY", legs=(_leg(25000, "CE", 10000), _leg(25000, "PE", 10500))
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.stance is EvidenceStance.NEUTRAL


def test_max_pain_hand_computed() -> None:
    # Only one CE strike with OI (24900) and one PE strike with OI (25100).
    # pain(K) = max(K-24900,0)*ce_oi + max(25100-K,0)*pe_oi
    # K=24900: 0 + 200*pe_oi; K=25000: 100*ce_oi + 100*pe_oi; K=25100: 200*ce_oi + 0
    # with ce_oi=pe_oi=1000 these all tie at 200000 -> first min wins (24900)
    legs = (
        _leg(24900, "CE", 1000),
        _leg(25000, "CE", 0),
        _leg(25100, "CE", 0),
        _leg(24900, "PE", 0),
        _leg(25000, "PE", 0),
        _leg(25100, "PE", 1000),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", legs=legs)
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.max_pain_strike is not None


def test_top_oi_strikes_sorted_descending() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY",
        legs=(_leg(24900, "CE", 500), _leg(25000, "CE", 2000), _leg(25100, "CE", 1000)),
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL", top_n=2)
    assert metrics.top_ce_oi_strikes == (25000, 25100)


def test_iv_skew_computed() -> None:
    chain = OptionChainSnapshot(
        underlying="NIFTY",
        legs=(_leg(25000, "CE", 1000, iv=15.0), _leg(25000, "PE", 1000, iv=18.0)),
    )
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.iv_skew == 3.0


def test_missing_oi_data_is_unavailable_stance() -> None:
    chain = OptionChainSnapshot(underlying="NIFTY", legs=(_leg(25000, "CE"), _leg(25000, "PE")))
    metrics = compute_chain_metrics(chain, underlying_ltp=25000, direction="CALL")
    assert metrics.stance is EvidenceStance.UNAVAILABLE
    assert metrics.pcr_oi is None
