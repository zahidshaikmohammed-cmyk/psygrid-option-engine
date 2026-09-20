"""Versioned TRADE_READY / NO_TRADE output schema. See docs/SIGNAL_SCHEMA.md.

This module is pure data shape + the invariants that can be checked purely
from the shape itself (e.g. reasons non-empty, R:R positive, SL/TP ordering
in premium space). It must not contain decision logic — anything that
*decides* a value belongs in signals/builder.py (Phase 10) or upstream
engines, not here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"
ENGINE_NAME: Literal["PSYGRID_OPTION_ENGINE"] = "PSYGRID_OPTION_ENGINE"

Underlying = Literal["NIFTY", "BANKNIFTY", "SENSEX"]
Direction = Literal["CALL", "PUT"]
OptionType = Literal["CE", "PE"]
SourceStatusLiteral = Literal["OK", "STALE", "MISSING", "ERROR"]
DataQualityOverall = Literal["GOOD", "DEGRADED", "INSUFFICIENT"]


class SourceStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    fetched_at: datetime | None = None
    observed_at: datetime | None = None
    age_seconds: float | None = None
    available: bool
    status: SourceStatusLiteral


class DataQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: DataQualityOverall
    critical_endpoints_ok: bool
    stale_fields: list[str] = Field(default_factory=list)
    unavailable_fields: list[str] = Field(default_factory=list)
    per_source: dict[str, SourceStatus] = Field(default_factory=dict)


class ContractRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    security_id: str
    symbol: str
    underlying: Underlying
    expiry: date
    strike: float = Field(gt=0)
    option_type: OptionType


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    risk_reward: float = Field(gt=0)
    structural_invalidation: str
    underlying_invalidation_level: float

    @model_validator(mode="after")
    def _check_ordering(self) -> ExecutionPlan:
        # Always a long-premium trade (buy CE or buy PE; see docs/SAFETY.md
        # and docs/SIGNAL_SCHEMA.md) so premium SL < entry < TP always holds,
        # regardless of CALL vs PUT.
        if not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError(
                "ExecutionPlan requires stop_loss < entry < take_profit in "
                f"premium space, got sl={self.stop_loss} entry={self.entry} "
                f"tp={self.take_profit}"
            )
        return self


class ExecutionQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    spread_pct: float = Field(ge=0)
    depth_assessment: str
    liquidity_assessment: str


class DevelopingSetup(BaseModel):
    """The strongest current setup even when it isn't (yet) actionable -
    brief section 2/22: a NO_TRADE outcome must still report current
    state, what's missing, and what would upgrade or invalidate it."""

    model_config = ConfigDict(frozen=True)

    framework: str
    direction: Direction
    tier: int = Field(ge=0, le=4)
    tier_label: str
    evidence_summary: list[str] = Field(default_factory=list)
    missing_confirmation: list[str] = Field(default_factory=list)
    upgrade_condition: str
    invalidation_condition: str


class SignalBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    engine: Literal["PSYGRID_OPTION_ENGINE"] = ENGINE_NAME
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_timestamp: datetime
    underlying: Underlying
    data_quality: DataQuality
    tier: int = Field(default=0, ge=0, le=4)
    market_state: dict = Field(default_factory=dict)
    structure: dict = Field(default_factory=dict)
    authorization: dict = Field(default_factory=dict)
    option_analysis: dict = Field(default_factory=dict)
    risk: dict = Field(default_factory=dict)
    reasons: list[str]

    @model_validator(mode="after")
    def _reasons_non_empty(self) -> SignalBase:
        if not self.reasons:
            raise ValueError("reasons must be non-empty")
        return self


class NoTradeSignal(SignalBase):
    state: Literal["NO_TRADE"] = "NO_TRADE"
    best_developing_setup: DevelopingSetup | None = None


class TradeReadySignal(SignalBase):
    state: Literal["TRADE_READY"] = "TRADE_READY"
    direction: Direction
    contract: ContractRef
    execution: ExecutionPlan
    execution_quality: ExecutionQuality

    @model_validator(mode="after")
    def _tier_must_be_actionable(self) -> TradeReadySignal:
        if self.tier < 1:
            raise ValueError("TRADE_READY signal must have tier >= 1")
        return self


Signal = NoTradeSignal | TradeReadySignal
