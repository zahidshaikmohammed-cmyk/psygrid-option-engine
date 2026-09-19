"""Objective, rule-based signal-tier requirements (brief sections 3 & 23).

Each tier's requirements are named, checkable conditions, not a cosmetic
score - a setup is TIER_3 only because it actually met five specific,
listed bars, and the assessment says exactly which ones. The thresholds
below are documented starting points, not statistically calibrated
probabilities (brief section 33) - once `replay/` has accumulated enough
outcome history, `calibration` work can revisit them with evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from psygrid_option_engine.authorization.confluence import ConfluenceReport

LiquidityQuality = str  # "GOOD" | "FAIR" | "POOR" | "UNKNOWN", from options/depth.py


class Tier(IntEnum):
    TIER_0 = 0
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4


TIER_LABELS: dict[Tier, str] = {
    Tier.TIER_0: "MARKET/SETUP ONLY",
    Tier.TIER_1: "LOW-CONFIDENCE ACTIONABLE",
    Tier.TIER_2: "VALID / MODERATE-CONFIDENCE",
    Tier.TIER_3: "HIGH-CONFLUENCE",
    Tier.TIER_4: "EXCEPTIONAL CONFLUENCE",
}

_LIQUIDITY_RANK = {"POOR": 0, "UNKNOWN": 0, "FAIR": 1, "GOOD": 2}


@dataclass(frozen=True)
class _Requirement:
    min_supportive: int
    max_conflicting: int
    min_risk_reward: float
    min_independent_streams: int
    min_liquidity: str | None
    require_good_data: bool
    max_unavailable: int | None


_REQUIREMENTS: dict[Tier, _Requirement] = {
    Tier.TIER_1: _Requirement(2, 1, 1.2, 1, None, False, None),
    Tier.TIER_2: _Requirement(3, 0, 1.5, 2, "FAIR", False, None),
    Tier.TIER_3: _Requirement(5, 0, 2.0, 2, "GOOD", True, None),
    Tier.TIER_4: _Requirement(7, 0, 2.5, 2, "GOOD", True, 1),
}


@dataclass(frozen=True)
class TierAssessment:
    tier: Tier
    label: str
    met: tuple[str, ...]
    missing: tuple[str, ...]


def _evaluate(
    req: _Requirement,
    *,
    confluence: ConfluenceReport,
    risk_reward: float | None,
    data_quality_overall: str,
    liquidity_quality: str,
    independent_streams: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    checks: dict[str, bool] = {
        f"supportive evidence >= {req.min_supportive}": confluence.supportive_count >= req.min_supportive,
        f"conflicting evidence <= {req.max_conflicting}": confluence.conflicting_count <= req.max_conflicting,
        f"risk/reward >= {req.min_risk_reward}": risk_reward is not None and risk_reward >= req.min_risk_reward,
        f"independent evidence streams >= {req.min_independent_streams}": (
            independent_streams >= req.min_independent_streams
        ),
    }
    if req.min_liquidity is not None:
        rank = _LIQUIDITY_RANK.get(liquidity_quality, 0)
        checks[f"option liquidity >= {req.min_liquidity}"] = rank >= _LIQUIDITY_RANK[req.min_liquidity]
    if req.require_good_data:
        checks["data quality == GOOD"] = data_quality_overall == "GOOD"
    else:
        checks["data quality != INSUFFICIENT"] = data_quality_overall != "INSUFFICIENT"
    if req.max_unavailable is not None:
        checks[f"unavailable evidence <= {req.max_unavailable}"] = confluence.unavailable_count <= req.max_unavailable

    met = tuple(k for k, ok in checks.items() if ok)
    missing = tuple(k for k, ok in checks.items() if not ok)
    return met, missing


def assess_tier(
    *,
    confluence: ConfluenceReport,
    risk_reward: float | None,
    data_quality_overall: str,
    liquidity_quality: str,
    independent_streams: int,
) -> TierAssessment:
    kwargs = dict(
        confluence=confluence,
        risk_reward=risk_reward,
        data_quality_overall=data_quality_overall,
        liquidity_quality=liquidity_quality,
        independent_streams=independent_streams,
    )
    for tier in (Tier.TIER_4, Tier.TIER_3, Tier.TIER_2, Tier.TIER_1):
        met, missing = _evaluate(_REQUIREMENTS[tier], **kwargs)  # type: ignore[arg-type]
        if not missing:
            return TierAssessment(tier, TIER_LABELS[tier], met, missing)

    # Nothing qualified for TIER_1 either; report what's missing against
    # the TIER_1 bar so callers can say exactly what would upgrade it.
    met, missing = _evaluate(_REQUIREMENTS[Tier.TIER_1], **kwargs)  # type: ignore[arg-type]
    return TierAssessment(Tier.TIER_0, TIER_LABELS[Tier.TIER_0], met, missing)
