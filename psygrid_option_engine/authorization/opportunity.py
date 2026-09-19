"""Opportunity generation (brief sections 21-22): evaluate every applicable
strategy framework for both directions, build the confluence/tier picture
for each, and identify the single best current opportunity.

This module does NOT select an option contract or compute entry/SL/TP -
it produces a directional, tier-rated candidate for `options/selection.py`
and `execution/engine.py` to act on next. It also always returns a
developing-setup summary even when nothing is actionable, so the caller
can report "no trade, but here is the state" instead of just silence
(brief section 2/22).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from psygrid_option_engine.authorization.confluence import ConfluenceReport, build_confluence
from psygrid_option_engine.authorization.frameworks import FrameworkContext, FrameworkResult, evaluate_frameworks
from psygrid_option_engine.authorization.tiers import Tier, TierAssessment, assess_tier
from psygrid_option_engine.domain.evidence import EvidenceItem

Direction = Literal["CALL", "PUT"]


@dataclass(frozen=True)
class Opportunity:
    underlying: str
    direction: Direction
    framework: FrameworkResult
    confluence: ConfluenceReport
    tier: TierAssessment
    risk_reward: float | None
    invalidation_level: float
    target_level: float | None
    evidence_summary: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class OpportunityScan:
    """Every candidate considered, and the best one (if any survives
    TIER_1+). `best_developing` is always populated when at least one
    framework was applicable, even if it never reached TIER_1 - this is
    what lets the caller report "no actionable trade, but here's the
    strongest developing setup" (brief section 22)."""

    candidates: tuple[Opportunity, ...] = field(default_factory=tuple)
    best: Opportunity | None = None
    best_developing: Opportunity | None = None


def _effective_target(framework: FrameworkResult, ctx: FrameworkContext) -> float | None:
    """Most frameworks (everything except TREND_CONTINUATION/
    COMPRESSION_EXPANSION) don't compute their own target - falling back
    to the session's expected-range boundary in the framework's direction
    keeps them from being structurally unable to ever reach an actionable
    tier, while still never fabricating a level: if the range model itself
    is unavailable, this stays None and the opportunity is correctly
    blocked on R:R, same as before."""
    if framework.target_level is not None:
        return framework.target_level
    if ctx.range_model is None:
        return None
    return ctx.range_model.upper_boundary if framework.direction == "CALL" else ctx.range_model.lower_boundary


def _risk_reward_proxy(framework: FrameworkResult, ltp: float | None, target: float | None) -> float | None:
    """A structure-space proxy R:R (reward distance / risk distance) used
    only to feed the tier gate before a real contract/premium exists.
    `execution/engine.py` computes the authoritative premium-space R:R
    later; this is deliberately conservative (falls back to None, which
    fails every tier's R:R requirement, rather than guessing)."""
    if ltp is None or target is None or framework.invalidation_level is None:
        return None
    risk = abs(ltp - framework.invalidation_level)
    reward = abs(target - ltp)
    if risk <= 0:
        return None
    return reward / risk


def build_opportunities(
    *,
    underlying: str,
    ctx: FrameworkContext,
    evidence_by_direction: dict[Direction, list[EvidenceItem]],
    data_quality_overall: str,
    liquidity_quality_by_direction: dict[Direction, str],
    independent_streams: int,
) -> OpportunityScan:
    """`evidence_by_direction`/`liquidity_quality_by_direction` are supplied
    by the caller (typically `api/decision.py`) since they depend on which
    option contract each direction would actually use - this module stays
    agnostic to option-selection details and only consumes the resulting
    evidence items.
    """
    candidates: list[Opportunity] = []

    for framework in evaluate_frameworks(ctx):
        if not framework.applicable or framework.direction is None or framework.invalidation_level is None:
            continue

        direction = framework.direction
        items = list(evidence_by_direction.get(direction, ()))
        confluence = build_confluence(items)
        target = _effective_target(framework, ctx)
        risk_reward = _risk_reward_proxy(framework, ctx.ltp, target)
        liquidity_quality = liquidity_quality_by_direction.get(direction, "UNKNOWN")

        tier = assess_tier(
            confluence=confluence,
            risk_reward=risk_reward,
            data_quality_overall=data_quality_overall,
            liquidity_quality=liquidity_quality,
            independent_streams=independent_streams,
        )

        candidates.append(
            Opportunity(
                underlying=underlying,
                direction=direction,
                framework=framework,
                confluence=confluence,
                tier=tier,
                risk_reward=risk_reward,
                invalidation_level=framework.invalidation_level,
                target_level=target,
                evidence_summary=tuple(f"{i.stream}: {i.detail}" for i in items),
                conflicts=confluence.contradictions,
            )
        )

    if not candidates:
        return OpportunityScan((), None, None)

    best_developing = max(candidates, key=lambda o: (o.tier.tier, o.confluence.supportive_count))
    actionable = [c for c in candidates if c.tier.tier >= Tier.TIER_1]
    best = max(actionable, key=lambda o: (o.tier.tier, o.confluence.supportive_count)) if actionable else None

    return OpportunityScan(tuple(candidates), best, best_developing)
