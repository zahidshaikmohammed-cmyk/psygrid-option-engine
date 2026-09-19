from __future__ import annotations

from datetime import UTC, datetime

import pytest

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import BreadthSnapshot, SectorSnapshot
from psygrid_option_engine.market.breadth import analyze_breadth

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def test_unavailable_when_no_breadth() -> None:
    ev = analyze_breadth(None, [], direction="CALL", underlying="NIFTY")
    assert ev.stance is EvidenceStance.UNAVAILABLE


def test_supportive_breadth_for_call() -> None:
    breadth = BreadthSnapshot(advances=_sf(1800), declines=_sf(500), unchanged=_sf(100))
    ev = analyze_breadth(breadth, [], direction="CALL", underlying="NIFTY")
    assert ev.advance_decline_ratio == 3.6
    assert ev.stance is EvidenceStance.SUPPORTIVE


def test_conflicting_breadth_for_call() -> None:
    breadth = BreadthSnapshot(advances=_sf(400), declines=_sf(1800), unchanged=_sf(100))
    ev = analyze_breadth(breadth, [], direction="CALL", underlying="NIFTY")
    assert ev.stance is EvidenceStance.CONFLICTING


def test_supportive_breadth_for_put_is_mirrored() -> None:
    breadth = BreadthSnapshot(advances=_sf(400), declines=_sf(1800), unchanged=_sf(100))
    ev = analyze_breadth(breadth, [], direction="PUT", underlying="NIFTY")
    assert ev.stance is EvidenceStance.SUPPORTIVE


def test_sector_participation_pct() -> None:
    breadth = BreadthSnapshot(advances=_sf(1000), declines=_sf(1000), unchanged=_sf(0))
    sectors = [
        SectorSnapshot(name="IT", change_pct=_sf(1.5)),
        SectorSnapshot(name="BANKING", change_pct=_sf(-0.5)),
        SectorSnapshot(name="AUTO", change_pct=_sf(2.0)),
    ]
    ev = analyze_breadth(breadth, sectors, direction="CALL", underlying="NIFTY")
    assert ev.supportive_sector_pct == pytest.approx((2 / 3) * 100)


def test_banknifty_banking_confirmation_note() -> None:
    breadth = BreadthSnapshot(advances=_sf(1000), declines=_sf(1000), unchanged=_sf(0))
    sectors = [SectorSnapshot(name="BANKING", change_pct=_sf(1.0))]
    ev = analyze_breadth(breadth, sectors, direction="CALL", underlying="BANKNIFTY")
    assert "banking sectors confirming" in ev.detail
