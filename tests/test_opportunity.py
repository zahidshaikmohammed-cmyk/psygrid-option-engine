from __future__ import annotations

from psygrid_option_engine.authorization.frameworks import FrameworkContext
from psygrid_option_engine.authorization.opportunity import build_opportunities
from psygrid_option_engine.authorization.tiers import Tier
from psygrid_option_engine.domain.evidence import EvidenceItem, EvidenceStance
from psygrid_option_engine.market.momentum import MomentumQuality
from psygrid_option_engine.market.volatility import RangeModel
from psygrid_option_engine.structure.types import (
    MarketRegime,
    RegimeAssessment,
    SessionLevels,
    StructureState,
    TrendBias,
)


def _regime(kind: MarketRegime) -> RegimeAssessment:
    return RegimeAssessment(regime=kind, evidence=("test",), independent_streams=2)


def _structure(bias: TrendBias, inv_long: float | None = 140, inv_short: float | None = 160) -> StructureState:
    return StructureState(
        swings=(), trend_bias=bias, bias_evidence=(), invalidation_long=inv_long, invalidation_short=inv_short
    )


def _range_model(upper: float | None = 170, lower: float | None = 130) -> RangeModel:
    return RangeModel(
        atr_daily=None,
        vix_implied_daily_move=None,
        session_range_so_far=None,
        expected_daily_range=None,
        expected_remaining_range=None,
        upper_boundary=upper,
        lower_boundary=lower,
        range_utilization_pct=None,
        time_remaining_minutes=None,
    )


def _base_ctx(**overrides: object) -> FrameworkContext:
    defaults: dict = dict(
        regime=_regime(MarketRegime.UNCERTAIN),
        structure=_structure(TrendBias.NEUTRAL),
        levels=SessionLevels(),
        liquidity_zones=(),
        level_reactions=(),
        ltp=150.0,
        vwap=None,
        vwap_relation=None,
        impulse=None,
        momentum_quality=None,
        pullback=None,
        range_model=None,
    )
    defaults.update(overrides)
    return FrameworkContext(**defaults)


def _supportive_items(n: int, stream_prefix: str = "s") -> list[EvidenceItem]:
    return [EvidenceItem(stream=f"{stream_prefix}{i}", stance=EvidenceStance.SUPPORTIVE, detail="x") for i in range(n)]


def test_no_applicable_frameworks_yields_empty_scan() -> None:
    ctx = _base_ctx()
    scan = build_opportunities(
        underlying="NIFTY",
        ctx=ctx,
        evidence_by_direction={"CALL": [], "PUT": []},
        data_quality_overall="GOOD",
        liquidity_quality_by_direction={"CALL": "GOOD", "PUT": "GOOD"},
        independent_streams=3,
    )
    assert scan.candidates == ()
    assert scan.best is None
    assert scan.best_developing is None


def test_strong_trend_with_supportive_evidence_reaches_actionable_tier() -> None:
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        momentum_quality=MomentumQuality.STRONG,
        range_model=_range_model(upper=170),
    )
    scan = build_opportunities(
        underlying="NIFTY",
        ctx=ctx,
        evidence_by_direction={"CALL": _supportive_items(3), "PUT": []},
        data_quality_overall="GOOD",
        liquidity_quality_by_direction={"CALL": "GOOD", "PUT": "UNKNOWN"},
        independent_streams=3,
    )
    assert scan.best is not None
    assert scan.best.direction == "CALL"
    assert scan.best.tier.tier >= Tier.TIER_1
    assert scan.best.risk_reward == 2.0  # reward 20 / risk 10


def test_weak_evidence_stays_developing_only() -> None:
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        momentum_quality=MomentumQuality.STRONG,
        range_model=_range_model(upper=170),
    )
    scan = build_opportunities(
        underlying="NIFTY",
        ctx=ctx,
        evidence_by_direction={"CALL": [], "PUT": []},  # no supportive evidence at all
        data_quality_overall="GOOD",
        liquidity_quality_by_direction={"CALL": "GOOD", "PUT": "UNKNOWN"},
        independent_streams=3,
    )
    assert scan.best is None
    assert scan.best_developing is not None
    assert scan.best_developing.tier.tier is Tier.TIER_0
    assert scan.best_developing.tier.missing  # explains what's missing to upgrade


def test_missing_target_level_blocks_risk_reward_and_tier() -> None:
    # No range_model -> framework.target_level is None -> risk_reward proxy is None
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        momentum_quality=MomentumQuality.STRONG,
        range_model=None,
    )
    scan = build_opportunities(
        underlying="NIFTY",
        ctx=ctx,
        evidence_by_direction={"CALL": _supportive_items(5), "PUT": []},
        data_quality_overall="GOOD",
        liquidity_quality_by_direction={"CALL": "GOOD", "PUT": "UNKNOWN"},
        independent_streams=3,
    )
    assert scan.best is None  # R:R unavailable -> every tier's R:R requirement fails
    assert scan.best_developing is not None
    assert scan.best_developing.risk_reward is None


def test_conflicting_evidence_prevents_actionable_tier() -> None:
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        momentum_quality=MomentumQuality.STRONG,
        range_model=_range_model(upper=170),
    )
    conflicting = [
        EvidenceItem(stream="futures", stance=EvidenceStance.CONFLICTING, detail="x"),
        EvidenceItem(stream="futures2", stance=EvidenceStance.CONFLICTING, detail="x"),
    ]
    scan = build_opportunities(
        underlying="NIFTY",
        ctx=ctx,
        evidence_by_direction={"CALL": conflicting, "PUT": []},
        data_quality_overall="GOOD",
        liquidity_quality_by_direction={"CALL": "GOOD", "PUT": "UNKNOWN"},
        independent_streams=3,
    )
    assert scan.best is None
    assert scan.best_developing is not None
    assert scan.best_developing.conflicts  # contradictions surfaced
