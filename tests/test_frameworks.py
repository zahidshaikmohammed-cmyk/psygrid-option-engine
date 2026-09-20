from __future__ import annotations

from psygrid_option_engine.authorization.frameworks import (
    FrameworkContext,
    FrameworkName,
    applicable_frameworks,
    evaluate_frameworks,
)
from psygrid_option_engine.market.momentum import MomentumQuality
from psygrid_option_engine.market.pullback import PullbackAssessment, PullbackType
from psygrid_option_engine.structure.types import (
    LevelReaction,
    LiquidityKind,
    LiquidityZone,
    MarketRegime,
    ReactionKind,
    RegimeAssessment,
    SessionLevels,
    StructureState,
    TrendBias,
)


def _regime(kind: MarketRegime) -> RegimeAssessment:
    return RegimeAssessment(regime=kind, evidence=("test",), independent_streams=2)


def _structure(bias: TrendBias, inv_long: float | None = 100, inv_short: float | None = 200) -> StructureState:
    return StructureState(
        swings=(), trend_bias=bias, bias_evidence=(), invalidation_long=inv_long, invalidation_short=inv_short
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


def test_trend_continuation_fires_on_trend_and_momentum() -> None:
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        momentum_quality=MomentumQuality.STRONG,
    )
    results = evaluate_frameworks(ctx)
    trend = next(r for r in results if r.framework is FrameworkName.TREND_CONTINUATION)
    assert trend.applicable is True
    assert trend.direction == "CALL"
    assert trend.invalidation_level == 140


def test_trend_continuation_not_applicable_in_range() -> None:
    ctx = _base_ctx(regime=_regime(MarketRegime.RANGE))
    trend = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.TREND_CONTINUATION)
    assert trend.applicable is False
    assert trend.prerequisites_failed


def test_structured_pullback_requires_shallow_or_moderate() -> None:
    pullback = PullbackAssessment(kind=PullbackType.SHALLOW, retracement_pct=20.0, legs=1, evidence=())
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP),
        structure=_structure(TrendBias.BULLISH, inv_long=140),
        pullback=pullback,
    )
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURED_PULLBACK)
    assert result.applicable is True
    assert result.direction == "CALL"


def test_structured_pullback_rejects_failed_pullback() -> None:
    pullback = PullbackAssessment(kind=PullbackType.FAILED, retracement_pct=110.0, legs=1, evidence=())
    ctx = _base_ctx(
        regime=_regime(MarketRegime.TRENDING_UP), structure=_structure(TrendBias.BULLISH), pullback=pullback
    )
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURED_PULLBACK)
    assert result.applicable is False


def test_liquidity_sweep_confirmation_bullish_when_zone_below_price() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDL, level=100.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.SWEEP)
    ctx = _base_ctx(regime=_regime(MarketRegime.LIQUIDITY_SWEEP), level_reactions=(reaction,), ltp=105.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.LIQUIDITY_SWEEP_CONFIRMATION)
    assert result.applicable is True
    assert result.direction == "CALL"
    assert result.invalidation_level == 100.0


def test_liquidity_sweep_confirmation_bearish_when_zone_above_price() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDH, level=200.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.SWEEP)
    ctx = _base_ctx(regime=_regime(MarketRegime.LIQUIDITY_SWEEP), level_reactions=(reaction,), ltp=195.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.LIQUIDITY_SWEEP_CONFIRMATION)
    assert result.direction == "PUT"


def test_range_boundary_rejection_at_resistance_is_bearish() -> None:
    zone = LiquidityZone(kind=LiquidityKind.SESSION_HIGH, level=200.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.REJECTION)
    ctx = _base_ctx(regime=_regime(MarketRegime.RANGE), level_reactions=(reaction,), ltp=195.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.RANGE_BOUNDARY_REJECTION)
    assert result.applicable is True
    assert result.direction == "PUT"


def test_failed_breakout_reversal_bullish_on_failed_breakdown() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDL, level=100.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.FAILED_BREAK)
    ctx = _base_ctx(regime=_regime(MarketRegime.FAILED_BREAKOUT), level_reactions=(reaction,), ltp=105.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.FAILED_BREAKOUT_REVERSAL)
    assert result.applicable is True
    assert result.direction == "CALL"


def test_confirmed_structural_breakout_bullish_on_acceptance_above_resistance() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDH, level=100.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.ACCEPTANCE)
    ctx = _base_ctx(regime=_regime(MarketRegime.BREAKOUT_ATTEMPT), level_reactions=(reaction,), ltp=105.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.CONFIRMED_STRUCTURAL_BREAKOUT)
    assert result.applicable is True
    assert result.direction == "CALL"


def test_compression_expansion_not_applicable_while_compressing() -> None:
    ctx = _base_ctx(regime=_regime(MarketRegime.COMPRESSION))
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.COMPRESSION_EXPANSION)
    assert result.applicable is False
    assert "awaiting" in result.prerequisites_failed[0]


def test_compression_expansion_fires_on_expansion_with_bias() -> None:
    ctx = _base_ctx(regime=_regime(MarketRegime.EXPANSION), structure=_structure(TrendBias.BULLISH, inv_long=140))
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.COMPRESSION_EXPANSION)
    assert result.applicable is True
    assert result.direction == "CALL"


def test_structural_reversal_bullish_on_rejection_at_major_support() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDL, level=100.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.REJECTION)
    ctx = _base_ctx(regime=_regime(MarketRegime.REVERSAL_ATTEMPT), level_reactions=(reaction,), ltp=105.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURAL_REVERSAL)
    assert result.applicable is True
    assert result.direction == "CALL"
    assert result.invalidation_level == 100.0


def test_structural_reversal_bearish_on_rejection_at_major_resistance() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PWH, level=200.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.REJECTION)
    ctx = _base_ctx(regime=_regime(MarketRegime.REVERSAL_ATTEMPT), level_reactions=(reaction,), ltp=195.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURAL_REVERSAL)
    assert result.applicable is True
    assert result.direction == "PUT"


def test_structural_reversal_ignores_non_major_level() -> None:
    zone = LiquidityZone(kind=LiquidityKind.EQUAL_HIGH, level=200.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.REJECTION)
    ctx = _base_ctx(regime=_regime(MarketRegime.REVERSAL_ATTEMPT), level_reactions=(reaction,), ltp=195.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURAL_REVERSAL)
    assert result.applicable is False


def test_structural_reversal_not_applicable_outside_reversal_regime() -> None:
    zone = LiquidityZone(kind=LiquidityKind.PDH, level=200.0, note="x")
    reaction = LevelReaction(zone=zone, reaction=ReactionKind.REJECTION)
    ctx = _base_ctx(regime=_regime(MarketRegime.RANGE), level_reactions=(reaction,), ltp=195.0)
    result = next(r for r in evaluate_frameworks(ctx) if r.framework is FrameworkName.STRUCTURAL_REVERSAL)
    assert result.applicable is False


def test_applicable_frameworks_filters_out_inapplicable() -> None:
    ctx = _base_ctx(regime=_regime(MarketRegime.UNCERTAIN))
    assert applicable_frameworks(ctx) == []


def test_no_framework_ever_forces_a_direction_without_evidence() -> None:
    # UNCERTAIN regime with everything else empty -> nothing applicable
    ctx = _base_ctx()
    results = evaluate_frameworks(ctx)
    assert all(not r.applicable for r in results)
