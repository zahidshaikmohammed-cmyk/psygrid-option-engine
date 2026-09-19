from __future__ import annotations

from psygrid_option_engine.authorization.confluence import ConfluenceReport
from psygrid_option_engine.authorization.tiers import Tier, assess_tier


def _confluence(supportive: int, conflicting: int, neutral: int = 0, unavailable: int = 0) -> ConfluenceReport:
    return ConfluenceReport(
        items=(),
        supportive_count=supportive,
        neutral_count=neutral,
        conflicting_count=conflicting,
        unavailable_count=unavailable,
        contradictions=(),
    )


def test_tier_0_when_nothing_qualifies() -> None:
    result = assess_tier(
        confluence=_confluence(0, 0),
        risk_reward=None,
        data_quality_overall="GOOD",
        liquidity_quality="GOOD",
        independent_streams=0,
    )
    assert result.tier is Tier.TIER_0
    assert result.missing  # should say exactly what's missing


def test_tier_1_minimum_bar() -> None:
    result = assess_tier(
        confluence=_confluence(2, 1),
        risk_reward=1.2,
        data_quality_overall="DEGRADED",
        liquidity_quality="POOR",
        independent_streams=1,
    )
    assert result.tier is Tier.TIER_1


def test_tier_2_requires_zero_conflicts() -> None:
    weak = assess_tier(
        confluence=_confluence(3, 1),  # one conflict blocks tier 2
        risk_reward=1.5,
        data_quality_overall="GOOD",
        liquidity_quality="FAIR",
        independent_streams=2,
    )
    assert weak.tier is Tier.TIER_1

    strong = assess_tier(
        confluence=_confluence(3, 0),
        risk_reward=1.5,
        data_quality_overall="GOOD",
        liquidity_quality="FAIR",
        independent_streams=2,
    )
    assert strong.tier is Tier.TIER_2


def test_tier_3_requires_good_liquidity_and_data() -> None:
    fair_liquidity = assess_tier(
        confluence=_confluence(5, 0),
        risk_reward=2.0,
        data_quality_overall="GOOD",
        liquidity_quality="FAIR",  # not GOOD -> blocks tier 3
        independent_streams=2,
    )
    assert fair_liquidity.tier is Tier.TIER_2

    good_liquidity = assess_tier(
        confluence=_confluence(5, 0),
        risk_reward=2.0,
        data_quality_overall="GOOD",
        liquidity_quality="GOOD",
        independent_streams=2,
    )
    assert good_liquidity.tier is Tier.TIER_3


def test_tier_4_requires_near_complete_evidence() -> None:
    mostly_unavailable = assess_tier(
        confluence=_confluence(7, 0, unavailable=3),
        risk_reward=2.5,
        data_quality_overall="GOOD",
        liquidity_quality="GOOD",
        independent_streams=2,
    )
    assert mostly_unavailable.tier is Tier.TIER_3

    complete = assess_tier(
        confluence=_confluence(7, 0, unavailable=1),
        risk_reward=2.5,
        data_quality_overall="GOOD",
        liquidity_quality="GOOD",
        independent_streams=2,
    )
    assert complete.tier is Tier.TIER_4


def test_higher_tier_never_awarded_on_weak_risk_reward() -> None:
    result = assess_tier(
        confluence=_confluence(10, 0),
        risk_reward=0.5,
        data_quality_overall="GOOD",
        liquidity_quality="GOOD",
        independent_streams=5,
    )
    assert result.tier is Tier.TIER_0


def test_insufficient_data_quality_blocks_even_tier_1() -> None:
    result = assess_tier(
        confluence=_confluence(10, 0),
        risk_reward=5.0,
        data_quality_overall="INSUFFICIENT",
        liquidity_quality="GOOD",
        independent_streams=5,
    )
    assert result.tier is Tier.TIER_0
