"""Final pre-trade risk gate (brief sections 13 & 26).

This module can only ever downgrade a candidate to NO_TRADE - it never
invents an entry/SL/TP or a contract; it only validates ones already
computed upstream (`options/selection.py`, `execution/engine.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from psygrid_option_engine.authorization.tiers import Tier, TierAssessment
from psygrid_option_engine.config.session import SessionWindow
from psygrid_option_engine.execution.engine import TradePlan
from psygrid_option_engine.options.selection import ContractCandidate

DEFAULT_MIN_RISK_REWARD = 1.2
DEFAULT_MAX_SPREAD_PCT = 5.0
_ACCEPTABLE_LIQUIDITY = ("GOOD", "FAIR")


@dataclass(frozen=True)
class RiskCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RiskValidationResult:
    passed: bool
    checks: tuple[RiskCheck, ...]
    failed_reasons: tuple[str, ...]


def validate_risk(
    *,
    plan: TradePlan | None,
    candidate: ContractCandidate | None,
    tier: TierAssessment,
    data_quality_overall: str,
    session_window: SessionWindow,
    as_of: datetime,
    expiry: date | None = None,
    min_risk_reward: float = DEFAULT_MIN_RISK_REWARD,
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
) -> RiskValidationResult:
    checks: list[RiskCheck] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(RiskCheck(name, passed, detail))

    add(
        "execution plan computed",
        plan is not None,
        "premium entry/SL/TP available" if plan is not None else "trade engineering produced no plan",
    )
    add(
        "contract selected",
        candidate is not None and not candidate.rejected,
        "a valid contract survived selection"
        if candidate is not None and not candidate.rejected
        else "no valid contract survived selection",
    )
    add("tier is actionable (>= TIER_1)", tier.tier >= Tier.TIER_1, f"assessed as {tier.label}")
    add(
        "data quality not INSUFFICIENT",
        data_quality_overall != "INSUFFICIENT",
        f"data_quality.overall = {data_quality_overall}",
    )
    entry_allowed = session_window.is_new_entry_allowed(as_of)
    add(
        "session allows new entries",
        entry_allowed,
        "within the entry window" if entry_allowed else "outside the session or past the entry cutoff",
    )

    if plan is not None:
        add(
            f"risk/reward >= {min_risk_reward:g}",
            plan.risk_reward >= min_risk_reward,
            f"R:R = {plan.risk_reward:.2f}",
        )
        add("stop_loss is positive", plan.stop_loss > 0, f"stop_loss = {plan.stop_loss:.2f}")
        add("entry below take_profit", plan.entry < plan.take_profit, "premium ordering valid")

    if candidate is not None and not candidate.rejected:
        spread_pct = candidate.depth.spread_pct
        add(
            f"option spread <= {max_spread_pct:g}%",
            spread_pct is not None and spread_pct <= max_spread_pct,
            f"spread = {spread_pct:.2f}%" if spread_pct is not None else "spread unavailable",
        )
        add(
            "option liquidity acceptable",
            candidate.depth.liquidity_quality in _ACCEPTABLE_LIQUIDITY,
            f"liquidity_quality = {candidate.depth.liquidity_quality}",
        )

    if expiry is not None:
        days_to_expiry = (expiry - as_of.date()).days
        add("expiry has not already passed", days_to_expiry >= 0, f"{days_to_expiry} day(s) to expiry")

    failed_reasons = tuple(f"{c.name}: {c.detail}" for c in checks if not c.passed)
    return RiskValidationResult(passed=not failed_reasons, checks=tuple(checks), failed_reasons=failed_reasons)
