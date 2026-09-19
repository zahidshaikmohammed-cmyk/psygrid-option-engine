from __future__ import annotations

from datetime import UTC, datetime

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import FuturesLeg, FuturesSnapshot
from psygrid_option_engine.market.futures_analysis import OiStance, analyze_futures

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def _futures(ltp: float, oi: float, oi_change: float) -> FuturesSnapshot:
    leg = FuturesLeg(
        expiry=None, ltp=_sf(ltp), oi=_sf(oi), oi_change=_sf(oi_change), volume=_sf(None), ohlc=None
    )
    return FuturesSnapshot(underlying="NIFTY", legs=(leg,))


def test_unavailable_when_no_futures() -> None:
    ev = analyze_futures(None, underlying_ltp=25000, price_change_pct=1.0, direction="CALL")
    assert ev.stance is EvidenceStance.UNAVAILABLE


def test_long_buildup_supportive_for_call() -> None:
    fut = _futures(ltp=25010, oi=100000, oi_change=5000)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=1.0, direction="CALL")
    assert ev.price_oi_relationship is OiStance.LONG_BUILDUP
    assert ev.stance is EvidenceStance.SUPPORTIVE


def test_short_buildup_conflicting_for_call() -> None:
    fut = _futures(ltp=24990, oi=100000, oi_change=5000)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=-1.0, direction="CALL")
    assert ev.price_oi_relationship is OiStance.SHORT_BUILDUP
    assert ev.stance is EvidenceStance.CONFLICTING


def test_short_buildup_supportive_for_put() -> None:
    fut = _futures(ltp=24990, oi=100000, oi_change=5000)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=-1.0, direction="PUT")
    assert ev.stance is EvidenceStance.SUPPORTIVE


def test_short_covering_supportive_for_call() -> None:
    fut = _futures(ltp=25010, oi=100000, oi_change=-5000)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=1.0, direction="CALL")
    assert ev.price_oi_relationship is OiStance.SHORT_COVERING
    assert ev.stance is EvidenceStance.SUPPORTIVE


def test_basis_pct_computed() -> None:
    fut = _futures(ltp=25050, oi=100000, oi_change=0)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=0.0, direction="CALL")
    assert ev.basis == 50
    assert ev.basis_pct == (50 / 25000) * 100


def test_missing_price_change_is_unclear_neutral() -> None:
    fut = _futures(ltp=25010, oi=100000, oi_change=5000)
    ev = analyze_futures(fut, underlying_ltp=25000, price_change_pct=None, direction="CALL")
    assert ev.price_oi_relationship is OiStance.UNCLEAR
    assert ev.stance is EvidenceStance.NEUTRAL
