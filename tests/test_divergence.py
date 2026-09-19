from __future__ import annotations

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.market.breadth import BreadthEvidence
from psygrid_option_engine.market.divergence import DivergenceKind, detect_divergences
from psygrid_option_engine.market.futures_analysis import FuturesEvidence, OiStance
from psygrid_option_engine.market.momentum import ImpulseMetrics


def test_no_divergence_when_everything_supportive() -> None:
    breadth = BreadthEvidence(2.0, 80.0, EvidenceStance.SUPPORTIVE, "ok")
    result = detect_divergences(
        price_change_pct=1.0, breadth=breadth, momentum=None, futures=None, direction="CALL"
    )
    assert result == []


def test_breadth_conflict_detected() -> None:
    breadth = BreadthEvidence(0.3, 20.0, EvidenceStance.CONFLICTING, "weak")
    result = detect_divergences(
        price_change_pct=1.0, breadth=breadth, momentum=None, futures=None, direction="CALL"
    )
    kinds = {d.kind for d in result}
    assert DivergenceKind.BREADTH_CONFLICT in kinds


def test_volume_weak_divergence() -> None:
    metrics = ImpulseMetrics(
        size=10, duration_bars=5, velocity=2, atr_normalized_move=1.0,
        volume_expansion_ratio=0.5, candle_efficiency=0.5, follow_through=True, acceleration=None,
    )
    result = detect_divergences(
        price_change_pct=1.0, breadth=None, momentum=metrics, futures=None, direction="CALL"
    )
    kinds = {d.kind for d in result}
    assert DivergenceKind.VOLUME_WEAK in kinds


def test_futures_conflict_detected() -> None:
    futures_ev = FuturesEvidence(10, 0.1, 5.0, OiStance.SHORT_BUILDUP, EvidenceStance.CONFLICTING, "x")
    result = detect_divergences(
        price_change_pct=1.0, breadth=None, momentum=None, futures=futures_ev, direction="CALL"
    )
    kinds = {d.kind for d in result}
    assert DivergenceKind.FUTURES_CONFLICT in kinds
