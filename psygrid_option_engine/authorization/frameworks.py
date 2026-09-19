"""Regime-aware strategy framework library (brief section 19).

Each framework declares its own prerequisites and only fires when the
*current regime* actually fits it - a trend framework is never forced into
a range, a breakout framework is never forced into compression, and a
liquidity-sweep framework never assumes every sweep reverses (it still
requires the SWEEP reaction itself as direct evidence, not an assumption).

Direction inference for the level-reaction-based frameworks follows one
consistent rule throughout this module: `structure/liquidity.py` tests a
zone "ABOVE" when it sits above the current price (a resistance test) and
"BELOW" when it sits below (a support test) - see
`classify_reactions_at_zones`. Every framework below reasons from that
same convention rather than re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from psygrid_option_engine.market.momentum import ImpulseMetrics, MomentumQuality
from psygrid_option_engine.market.pullback import PullbackAssessment, PullbackType
from psygrid_option_engine.market.volatility import RangeModel
from psygrid_option_engine.structure.types import (
    LevelReaction,
    LiquidityZone,
    MarketRegime,
    ReactionKind,
    RegimeAssessment,
    SessionLevels,
    StructureState,
    TrendBias,
)

Direction = Literal["CALL", "PUT"]


class FrameworkName(StrEnum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    STRUCTURED_PULLBACK = "STRUCTURED_PULLBACK"
    LIQUIDITY_SWEEP_CONFIRMATION = "LIQUIDITY_SWEEP_CONFIRMATION"
    RANGE_BOUNDARY_REJECTION = "RANGE_BOUNDARY_REJECTION"
    FAILED_BREAKOUT_REVERSAL = "FAILED_BREAKOUT_REVERSAL"
    CONFIRMED_STRUCTURAL_BREAKOUT = "CONFIRMED_STRUCTURAL_BREAKOUT"
    COMPRESSION_EXPANSION = "COMPRESSION_EXPANSION"


@dataclass(frozen=True)
class FrameworkContext:
    regime: RegimeAssessment
    structure: StructureState
    levels: SessionLevels
    liquidity_zones: tuple[LiquidityZone, ...]
    level_reactions: tuple[LevelReaction, ...]
    ltp: float | None
    vwap: float | None
    vwap_relation: str | None
    impulse: ImpulseMetrics | None
    momentum_quality: MomentumQuality | None
    pullback: PullbackAssessment | None
    range_model: RangeModel | None


@dataclass(frozen=True)
class FrameworkResult:
    framework: FrameworkName
    applicable: bool
    direction: Direction | None
    invalidation_level: float | None
    target_level: float | None
    trigger: str
    prerequisites_met: tuple[str, ...]
    prerequisites_failed: tuple[str, ...]


def _nearest_reaction(
    reactions: tuple[LevelReaction, ...], kind: ReactionKind, ltp: float | None
) -> LevelReaction | None:
    candidates = [r for r in reactions if r.reaction is kind]
    if not candidates or ltp is None:
        return candidates[0] if candidates else None
    return min(candidates, key=lambda r: abs(r.zone.level - ltp))


def _not_applicable(name: FrameworkName, failed: tuple[str, ...]) -> FrameworkResult:
    return FrameworkResult(name, False, None, None, None, "", (), failed)


def _trend_continuation(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.TREND_CONTINUATION
    if ctx.regime.regime not in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
        return _not_applicable(name, ("regime is not a confirmed trend",))
    if ctx.momentum_quality not in (MomentumQuality.STRONG, MomentumQuality.MODERATE):
        return _not_applicable(name, ("momentum quality is not at least MODERATE",))

    bullish = ctx.regime.regime is MarketRegime.TRENDING_UP
    direction: Direction = "CALL" if bullish else "PUT"
    invalidation = ctx.structure.invalidation_long if bullish else ctx.structure.invalidation_short
    if invalidation is None:
        return _not_applicable(name, ("no structural invalidation level available",))

    target = ctx.range_model.upper_boundary if (bullish and ctx.range_model) else (
        ctx.range_model.lower_boundary if ctx.range_model else None
    )
    met = ("regime confirms trend", f"momentum quality {ctx.momentum_quality.value}")
    return FrameworkResult(
        name, True, direction, invalidation, target, "trend regime with confirming momentum", met, ()
    )


def _structured_pullback(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.STRUCTURED_PULLBACK
    if ctx.regime.regime not in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.PULLBACK):
        return _not_applicable(name, ("regime does not support a pullback-continuation read",))
    if ctx.pullback is None or ctx.pullback.kind not in (PullbackType.SHALLOW, PullbackType.MODERATE):
        return _not_applicable(name, ("no shallow/moderate pullback currently in evidence",))
    if ctx.structure.trend_bias is TrendBias.NEUTRAL:
        return _not_applicable(name, ("no directional structure bias to resume",))

    bullish = ctx.structure.trend_bias is TrendBias.BULLISH
    direction: Direction = "CALL" if bullish else "PUT"
    invalidation = ctx.structure.invalidation_long if bullish else ctx.structure.invalidation_short
    if invalidation is None:
        return _not_applicable(name, ("no structural invalidation level available",))

    met = (f"pullback kind {ctx.pullback.kind.value}", "trend bias intact")
    return FrameworkResult(
        name, True, direction, invalidation, None, f"{ctx.pullback.kind.value} pullback within trend", met, ()
    )


def _liquidity_sweep_confirmation(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.LIQUIDITY_SWEEP_CONFIRMATION
    if ctx.regime.regime is not MarketRegime.LIQUIDITY_SWEEP:
        return _not_applicable(name, ("regime is not LIQUIDITY_SWEEP",))
    reaction = _nearest_reaction(ctx.level_reactions, ReactionKind.SWEEP, ctx.ltp)
    if reaction is None or ctx.ltp is None:
        return _not_applicable(name, ("no sweep reaction found at a tracked liquidity zone",))

    # zone below price (a support test) swept then reclaimed -> bullish;
    # zone above price (a resistance test) swept then rejected -> bearish.
    bullish = reaction.zone.level < ctx.ltp
    direction: Direction = "CALL" if bullish else "PUT"
    met = (f"sweep confirmed at {reaction.zone.kind.value} ({reaction.zone.level:g})",)
    return FrameworkResult(
        name, True, direction, reaction.zone.level, None, f"liquidity sweep at {reaction.zone.kind.value}", met, ()
    )


def _range_boundary_rejection(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.RANGE_BOUNDARY_REJECTION
    if ctx.regime.regime is not MarketRegime.RANGE:
        return _not_applicable(name, ("regime is not RANGE",))
    reaction = _nearest_reaction(ctx.level_reactions, ReactionKind.REJECTION, ctx.ltp)
    if reaction is None or ctx.ltp is None:
        return _not_applicable(name, ("no rejection reaction found at a tracked liquidity zone",))

    # rejection at a resistance (zone above price) -> bearish; at a
    # support (zone below price) -> bullish.
    bullish = reaction.zone.level < ctx.ltp
    direction: Direction = "CALL" if bullish else "PUT"
    met = (f"rejection confirmed at {reaction.zone.kind.value} ({reaction.zone.level:g})",)
    trigger = f"range boundary rejection at {reaction.zone.kind.value}"
    return FrameworkResult(name, True, direction, reaction.zone.level, None, trigger, met, ())


def _failed_breakout_reversal(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.FAILED_BREAKOUT_REVERSAL
    if ctx.regime.regime is not MarketRegime.FAILED_BREAKOUT:
        return _not_applicable(name, ("regime is not FAILED_BREAKOUT",))
    reaction = _nearest_reaction(ctx.level_reactions, ReactionKind.FAILED_BREAK, ctx.ltp)
    if reaction is None or ctx.ltp is None:
        return _not_applicable(name, ("no failed-break reaction found at a tracked liquidity zone",))

    # failed breakdown (support test, zone below price) -> bullish;
    # failed breakout (resistance test, zone above price) -> bearish.
    bullish = reaction.zone.level < ctx.ltp
    direction: Direction = "CALL" if bullish else "PUT"
    met = (f"failed break confirmed at {reaction.zone.kind.value} ({reaction.zone.level:g})",)
    trigger = f"failed breakout/breakdown at {reaction.zone.kind.value}"
    return FrameworkResult(name, True, direction, reaction.zone.level, None, trigger, met, ())


def _confirmed_structural_breakout(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.CONFIRMED_STRUCTURAL_BREAKOUT
    if ctx.regime.regime is not MarketRegime.BREAKOUT_ATTEMPT:
        return _not_applicable(name, ("regime is not BREAKOUT_ATTEMPT",))
    reaction = _nearest_reaction(ctx.level_reactions, ReactionKind.ACCEPTANCE, ctx.ltp)
    if reaction is None or ctx.ltp is None:
        return _not_applicable(name, ("no acceptance reaction found at a tracked liquidity zone",))

    # acceptance above a resistance -> bullish; acceptance below a support -> bearish.
    bullish = reaction.zone.level < ctx.ltp
    direction: Direction = "CALL" if bullish else "PUT"
    met = (f"acceptance confirmed beyond {reaction.zone.kind.value} ({reaction.zone.level:g})",)
    trigger = f"structural breakout confirmed at {reaction.zone.kind.value}"
    return FrameworkResult(name, True, direction, reaction.zone.level, None, trigger, met, ())


def _compression_expansion(ctx: FrameworkContext) -> FrameworkResult:
    name = FrameworkName.COMPRESSION_EXPANSION
    if ctx.regime.regime is MarketRegime.COMPRESSION:
        return _not_applicable(name, ("volatility still compressing - awaiting an expansion trigger",))
    if ctx.regime.regime is not MarketRegime.EXPANSION:
        return _not_applicable(name, ("regime is not EXPANSION",))
    if ctx.structure.trend_bias is TrendBias.NEUTRAL:
        return _not_applicable(name, ("expansion has no directional structure bias yet",))

    bullish = ctx.structure.trend_bias is TrendBias.BULLISH
    direction: Direction = "CALL" if bullish else "PUT"
    invalidation = ctx.structure.invalidation_long if bullish else ctx.structure.invalidation_short
    if invalidation is None:
        return _not_applicable(name, ("no structural invalidation level available",))
    met = ("volatility expansion confirmed", "structure bias directional")
    return FrameworkResult(
        name, True, direction, invalidation, None, "volatility expansion with directional structure", met, ()
    )


_ALL_FRAMEWORKS = (
    _trend_continuation,
    _structured_pullback,
    _liquidity_sweep_confirmation,
    _range_boundary_rejection,
    _failed_breakout_reversal,
    _confirmed_structural_breakout,
    _compression_expansion,
)


def evaluate_frameworks(ctx: FrameworkContext) -> list[FrameworkResult]:
    return [fn(ctx) for fn in _ALL_FRAMEWORKS]


def applicable_frameworks(ctx: FrameworkContext) -> list[FrameworkResult]:
    return [r for r in evaluate_frameworks(ctx) if r.applicable]
