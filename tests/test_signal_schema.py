from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from psygrid_option_engine.signals.schema import (
    ContractRef,
    DataQuality,
    DevelopingSetup,
    ExecutionPlan,
    ExecutionQuality,
    NoTradeSignal,
    TradeReadySignal,
)

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _data_quality(**overrides: object) -> DataQuality:
    base = dict(overall="GOOD", critical_endpoints_ok=True)
    base.update(overrides)
    return DataQuality(**base)  # type: ignore[arg-type]


def test_no_trade_requires_non_empty_reasons() -> None:
    with pytest.raises(ValidationError):
        NoTradeSignal(
            decision_timestamp=NOW,
            underlying="NIFTY",
            data_quality=_data_quality(),
            reasons=[],
        )


def test_no_trade_minimal_valid() -> None:
    sig = NoTradeSignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=_data_quality(overall="INSUFFICIENT", critical_endpoints_ok=False),
        reasons=["critical endpoint 'options' failed: HTTP 503"],
    )
    assert sig.state == "NO_TRADE"
    assert sig.schema_version == "1.0"
    assert sig.engine == "PSYGRID_OPTION_ENGINE"


def _contract() -> ContractRef:
    return ContractRef(
        security_id="ABC123",
        symbol="NIFTY25SEP24500CE",
        underlying="NIFTY",
        expiry=date(2026, 9, 25),
        strike=24500,
        option_type="CE",
    )


def _execution(**overrides: object) -> dict:
    base = dict(
        entry=120.0,
        stop_loss=90.0,
        take_profit=180.0,
        risk_reward=2.0,
        structural_invalidation="close below opening range low",
        underlying_invalidation_level=24400.0,
    )
    base.update(overrides)
    return base


def test_execution_plan_requires_sl_below_entry_below_tp() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(**_execution(stop_loss=150.0))  # sl > entry, invalid


def test_execution_plan_valid_ordering() -> None:
    plan = ExecutionPlan(**_execution())
    assert plan.stop_loss < plan.entry < plan.take_profit


def test_trade_ready_full_signal() -> None:
    sig = TradeReadySignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=_data_quality(),
        reasons=["structure: higher-high confirmed above opening range"],
        tier=2,
        direction="CALL",
        contract=_contract(),
        execution=ExecutionPlan(**_execution()),
        execution_quality=ExecutionQuality(
            spread_pct=0.8, depth_assessment="adequate", liquidity_assessment="adequate"
        ),
    )
    assert sig.state == "TRADE_READY"
    dumped = sig.model_dump(mode="json")
    assert dumped["contract"]["option_type"] == "CE"
    assert dumped["execution"]["risk_reward"] == 2.0


def test_strike_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ContractRef(
            security_id="x",
            symbol="x",
            underlying="NIFTY",
            expiry=date(2026, 9, 25),
            strike=0,
            option_type="CE",
        )


def test_schema_version_pinned() -> None:
    with pytest.raises(ValidationError):
        NoTradeSignal(
            schema_version="2.0",  # type: ignore[arg-type]
            decision_timestamp=NOW,
            underlying="NIFTY",
            data_quality=_data_quality(),
            reasons=["x"],
        )


def test_no_trade_default_tier_is_zero() -> None:
    sig = NoTradeSignal(
        decision_timestamp=NOW, underlying="NIFTY", data_quality=_data_quality(), reasons=["x"]
    )
    assert sig.tier == 0


def test_trade_ready_rejects_tier_zero() -> None:
    with pytest.raises(ValidationError):
        TradeReadySignal(
            decision_timestamp=NOW,
            underlying="NIFTY",
            data_quality=_data_quality(),
            reasons=["x"],
            tier=0,
            direction="CALL",
            contract=_contract(),
            execution=ExecutionPlan(**_execution()),
            execution_quality=ExecutionQuality(
                spread_pct=0.8, depth_assessment="adequate", liquidity_assessment="adequate"
            ),
        )


def test_tier_out_of_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        NoTradeSignal(
            decision_timestamp=NOW,
            underlying="NIFTY",
            data_quality=_data_quality(),
            reasons=["x"],
            tier=5,
        )


def test_no_trade_carries_best_developing_setup() -> None:
    setup = DevelopingSetup(
        framework="TREND_CONTINUATION",
        direction="CALL",
        tier=0,
        tier_label="MARKET/SETUP ONLY",
        evidence_summary=["structure bullish"],
        missing_confirmation=["need R:R >= 1.2"],
        upgrade_condition="risk/reward improves to at least 1.2",
        invalidation_condition="price closes below the last swing low",
    )
    sig = NoTradeSignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=_data_quality(),
        reasons=["no actionable opportunity; best developing setup reported"],
        best_developing_setup=setup,
    )
    assert sig.best_developing_setup is not None
    assert sig.best_developing_setup.upgrade_condition.startswith("risk/reward")
