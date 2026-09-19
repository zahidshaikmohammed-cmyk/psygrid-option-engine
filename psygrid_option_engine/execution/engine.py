"""Premium execution engineering (brief sections 11-12, 25-26).

The option premium stop/target are derived from the underlying's own
structural invalidation and target levels via the contract's delta - never
an arbitrary `SL = -20%`. If delta is unavailable, this engine refuses to
engineer a trade rather than guess (a `NO_TRADE`-worthy outcome, per
`risk/validation.py`), consistent with "do not manufacture certainty
merely to produce a signal" (brief section 2).

`safety_margin` inflates the premium stop distance (never the target) to
account for gamma - delta alone understates how fast an option loses value
as the underlying accelerates against the position. It is a documented v1
constant, not a statistically fitted number (brief section 33).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psygrid_option_engine.domain.snapshot import OptionLeg

Direction = Literal["CALL", "PUT"]

DEFAULT_SAFETY_MARGIN = 1.15


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    structural_invalidation_underlying: float
    structural_invalidation_description: str
    delta_used: float
    safety_margin: float


@dataclass(frozen=True)
class ExecutionResult:
    plan: TradePlan | None
    rejection_reason: str | None


def engineer_trade(
    leg: OptionLeg,
    *,
    direction: Direction,
    underlying_ltp: float | None,
    invalidation_level: float | None,
    target_level: float | None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
) -> ExecutionResult:
    if underlying_ltp is None or invalidation_level is None:
        return ExecutionResult(None, "missing underlying price or structural invalidation level")
    if target_level is None:
        return ExecutionResult(None, "no target level available to compute take profit")
    if not leg.delta.available or leg.delta.value is None:
        return ExecutionResult(None, "option delta unavailable - cannot translate underlying risk to premium risk")

    entry = leg.ltp.value if (leg.ltp.available and leg.ltp.value and leg.ltp.value > 0) else None
    if entry is None:
        if leg.bid.value is not None and leg.ask.value is not None:
            entry = (leg.bid.value + leg.ask.value) / 2
        else:
            return ExecutionResult(None, "no tradable premium price available (missing LTP and bid/ask)")

    underlying_stop_distance = abs(underlying_ltp - invalidation_level)
    underlying_target_distance = abs(target_level - underlying_ltp)
    if underlying_stop_distance <= 0:
        return ExecutionResult(None, "structural invalidation level coincides with current underlying price")

    abs_delta = abs(leg.delta.value)
    if abs_delta <= 0:
        return ExecutionResult(None, "option delta is zero - premium would not respond to underlying movement")

    premium_stop_distance = abs_delta * underlying_stop_distance * safety_margin
    premium_target_distance = abs_delta * underlying_target_distance

    stop_loss = entry - premium_stop_distance
    take_profit = entry + premium_target_distance

    if stop_loss <= 0:
        return ExecutionResult(
            None,
            f"computed stop_loss ({stop_loss:.2f}) is not positive - "
            "structural stop is too wide relative to this contract's premium",
        )
    if take_profit <= entry:
        return ExecutionResult(None, "computed take_profit does not exceed entry")

    risk_reward = premium_target_distance / premium_stop_distance
    description = f"underlying {direction} thesis invalidates at {invalidation_level:g} (structural stop)"

    plan = TradePlan(
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        structural_invalidation_underlying=invalidation_level,
        structural_invalidation_description=description,
        delta_used=abs_delta,
        safety_margin=safety_margin,
    )
    return ExecutionResult(plan, None)
