from __future__ import annotations

from datetime import UTC, date, datetime

from psygrid_option_engine.authorization.tiers import Tier, TierAssessment
from psygrid_option_engine.config.session import SessionWindow
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import OptionLeg
from psygrid_option_engine.execution.engine import TradePlan
from psygrid_option_engine.options.depth import DepthMetrics
from psygrid_option_engine.options.selection import ContractCandidate
from psygrid_option_engine.risk.validation import validate_risk

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)  # 10:30 IST, within session
OUTSIDE_SESSION = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)


def _sf(v: float | None) -> SourcedField[float]:
    if v is None:
        return SourcedField.missing("x")
    return SourcedField.of(v, source="x", observed_at=NOW, fetched_at=NOW)


def _leg() -> OptionLeg:
    return OptionLeg(
        security_id="A", symbol="X", strike=25000, option_type="CE", expiry=date(2026, 9, 25),
        ltp=_sf(100), bid=_sf(99), ask=_sf(101), volume=_sf(1000), oi=_sf(10000), oi_change=_sf(None),
        iv=_sf(None), delta=_sf(0.5), gamma=_sf(None), theta=_sf(None), vega=_sf(None),
    )


def _plan(
    risk_reward: float = 2.0, stop_loss: float = 80.0, entry: float = 100.0, take_profit: float = 140.0
) -> TradePlan:
    return TradePlan(
        entry=entry, stop_loss=stop_loss, take_profit=take_profit, risk_reward=risk_reward,
        structural_invalidation_underlying=24950, structural_invalidation_description="x",
        delta_used=0.5, safety_margin=1.15,
    )


def _candidate(spread_pct: float = 1.0, liquidity: str = "GOOD") -> ContractCandidate:
    depth = DepthMetrics(99, 101, 2, spread_pct, None, None, 500, 500, liquidity)  # type: ignore[arg-type]
    return ContractCandidate(leg=_leg(), depth=depth, score=0.8, rejected=False, rejection_reason=None, reasons=())


def _tier(t: Tier = Tier.TIER_2) -> TierAssessment:
    return TierAssessment(tier=t, label=t.name, met=(), missing=())


def test_all_checks_pass() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW, expiry=date(2026, 9, 25),
    )
    assert result.passed is True
    assert result.failed_reasons == ()


def test_missing_plan_fails() -> None:
    result = validate_risk(
        plan=None, candidate=_candidate(), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW,
    )
    assert result.passed is False
    assert any("execution plan" in r for r in result.failed_reasons)


def test_tier_0_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(), tier=_tier(Tier.TIER_0), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW,
    )
    assert result.passed is False
    assert any("tier is actionable" in r for r in result.failed_reasons)


def test_outside_session_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=OUTSIDE_SESSION,
    )
    assert result.passed is False
    assert any("session" in r for r in result.failed_reasons)


def test_low_risk_reward_fails() -> None:
    result = validate_risk(
        plan=_plan(risk_reward=0.5), candidate=_candidate(), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW, min_risk_reward=1.2,
    )
    assert result.passed is False
    assert any("risk/reward" in r for r in result.failed_reasons)


def test_wide_spread_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(spread_pct=10.0), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW, max_spread_pct=5.0,
    )
    assert result.passed is False
    assert any("spread" in r for r in result.failed_reasons)


def test_poor_liquidity_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(liquidity="POOR"), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW,
    )
    assert result.passed is False
    assert any("liquidity" in r for r in result.failed_reasons)


def test_expired_contract_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(), tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW, expiry=date(2026, 9, 10),
    )
    assert result.passed is False
    assert any("expiry" in r for r in result.failed_reasons)


def test_insufficient_data_quality_fails() -> None:
    result = validate_risk(
        plan=_plan(), candidate=_candidate(), tier=_tier(), data_quality_overall="INSUFFICIENT",
        session_window=SessionWindow(), as_of=NOW,
    )
    assert result.passed is False
    assert any("data quality" in r for r in result.failed_reasons)


def test_rejected_candidate_fails() -> None:
    rejected = ContractCandidate(
        leg=_leg(), depth=_candidate().depth, score=None, rejected=True,
        rejection_reason="low OI", reasons=(),
    )
    result = validate_risk(
        plan=_plan(), candidate=rejected, tier=_tier(), data_quality_overall="GOOD",
        session_window=SessionWindow(), as_of=NOW,
    )
    assert result.passed is False
    assert any("contract selected" in r for r in result.failed_reasons)
