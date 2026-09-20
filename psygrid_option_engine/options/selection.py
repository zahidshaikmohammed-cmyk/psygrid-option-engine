"""Constrained contract selection (brief sections 10 & 24).

Deliberately NOT "always ATM" / "highest OI" / "highest volume" /
"nearest strike" / "cheapest": candidates are filtered by hard liquidity
and delta-band constraints, then the survivors are scored on a documented,
named combination of liquidity, spread tightness, and delta fit. Every
rejected candidate keeps its rejection reason for the audit trail (brief
section 31) - nothing is silently dropped.

Scoring weights and thresholds are named constants with a comment, not
buried magic numbers, and are explicitly v1 starting points pending
calibration against real execution outcomes (brief section 33) - never
presented as validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from psygrid_option_engine.domain.snapshot import (
    DepthSnapshot,
    InstrumentDepth,
    OptionChainSnapshot,
    OptionLeg,
    OptionType,
)
from psygrid_option_engine.options.depth import DepthMetrics, LiquidityQuality, compute_depth_metrics

Direction = Literal["CALL", "PUT"]

# v1 scoring weights - starting points, not statistically calibrated.
_WEIGHT_LIQUIDITY = 0.40
_WEIGHT_TIGHTNESS = 0.35
_WEIGHT_DELTA_FIT = 0.25


@dataclass(frozen=True)
class ContractCandidate:
    leg: OptionLeg
    depth: DepthMetrics
    score: float | None
    rejected: bool
    rejection_reason: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SelectionResult:
    selected: ContractCandidate | None
    candidates: tuple[ContractCandidate, ...]


def _direction_to_option_type(direction: Direction) -> OptionType:
    return "CE" if direction == "CALL" else "PE"


def _fallback_depth_from_leg(leg: OptionLeg) -> DepthMetrics:
    """When no 20-level depth is available for this security, fall back to
    a coarse spread read from the leg's own bid/ask so selection isn't
    forced to reject every candidate outright when only depth.json is
    missing/stale."""
    bid, ask = leg.bid.value, leg.ask.value
    spread: float | None = None
    mid: float | None = leg.ltp.value
    if bid is not None and ask is not None:
        spread = ask - bid
        mid = (bid + ask) / 2
    spread_pct = (spread / mid * 100) if (spread is not None and mid) else None
    quality: LiquidityQuality = "UNKNOWN" if spread_pct is None else ("FAIR" if spread_pct <= 5.0 else "POOR")
    return DepthMetrics(bid, ask, spread, spread_pct, None, None, None, None, quality)


def select_contract(
    chain: OptionChainSnapshot | None,
    *,
    direction: Direction,
    underlying_ltp: float | None,
    depth: DepthSnapshot | None = None,
    expiry: date | None = None,
    target_delta: float = 0.5,
    delta_band: float = 0.15,
    max_spread_pct: float = 5.0,
    min_oi: float = 500.0,
    min_volume: float = 0.0,
) -> SelectionResult:
    if chain is None or not chain.legs or underlying_ltp is None:
        return SelectionResult(None, ())

    option_type = _direction_to_option_type(direction)
    candidate_expiry = expiry or (min(chain.expiries()) if chain.expiries() else None)
    legs = [
        leg
        for leg in chain.legs
        if leg.option_type == option_type and (candidate_expiry is None or leg.expiry == candidate_expiry)
    ]
    if not legs:
        return SelectionResult(None, ())

    evaluated: list[ContractCandidate] = []
    survivors: list[tuple[ContractCandidate, float, float, float | None]] = []

    for leg in legs:
        instrument_depth: InstrumentDepth | None = depth.by_security_id.get(leg.security_id) if depth else None
        # Real 20-level depth is preferred, but only when it actually has
        # at least one genuinely-quoted level - an InstrumentDepth object
        # can exist with empty bids/asks (e.g. an all-zero-price ladder
        # already filtered out in data/snapshot_builder.py), in which case
        # it carries no more information than "no real depth" and the
        # option chain's own bid/ask is a strictly better source than
        # reporting UNKNOWN liquidity outright.
        has_real_levels = instrument_depth is not None and (instrument_depth.bids or instrument_depth.asks)
        depth_metrics = compute_depth_metrics(instrument_depth) if has_real_levels else _fallback_depth_from_leg(leg)

        rejection = _hard_reject_reason(
            leg, depth_metrics, target_delta=target_delta, delta_band=delta_band,
            max_spread_pct=max_spread_pct, min_oi=min_oi, min_volume=min_volume,
        )
        if rejection is not None:
            evaluated.append(ContractCandidate(leg, depth_metrics, None, True, rejection, ()))
            continue

        oi = leg.oi.value or 0.0
        spread_pct = depth_metrics.spread_pct if depth_metrics.spread_pct is not None else max_spread_pct
        delta_distance = None
        if leg.delta.available and leg.delta.value is not None:
            delta_distance = abs(abs(leg.delta.value) - target_delta)
        base = ContractCandidate(leg, depth_metrics, None, False, None, ())
        survivors.append((base, oi, spread_pct, delta_distance))

    if survivors:
        ois = [s[1] for s in survivors]
        spreads = [s[2] for s in survivors]
        oi_lo, oi_hi = min(ois), max(ois)
        spread_lo, spread_hi = min(spreads), max(spreads)

        scored: list[ContractCandidate] = []
        for candidate, oi, spread_pct, delta_distance in survivors:
            liquidity_norm = 1.0 if oi_hi == oi_lo else (oi - oi_lo) / (oi_hi - oi_lo)
            tightness_norm = 1.0 if spread_hi == spread_lo else 1.0 - (spread_pct - spread_lo) / (spread_hi - spread_lo)
            fit_norm = 1.0 - (delta_distance / delta_band) if delta_distance is not None else 0.5

            score = (
                _WEIGHT_LIQUIDITY * liquidity_norm
                + _WEIGHT_TIGHTNESS * tightness_norm
                + _WEIGHT_DELTA_FIT * max(0.0, fit_norm)
            )
            reasons = (
                f"OI {oi:g} (liquidity score {liquidity_norm:.2f})",
                f"spread {spread_pct:.2f}% (tightness score {tightness_norm:.2f})",
                f"delta fit score {max(0.0, fit_norm):.2f}",
            )
            scored.append(
                ContractCandidate(candidate.leg, candidate.depth, score, False, None, reasons)
            )
        evaluated.extend(scored)
        selected = max(scored, key=lambda c: c.score if c.score is not None else -1.0)
    else:
        selected = None

    return SelectionResult(selected, tuple(evaluated))


def _hard_reject_reason(
    leg: OptionLeg,
    depth_metrics: DepthMetrics,
    *,
    target_delta: float,
    delta_band: float,
    max_spread_pct: float,
    min_oi: float,
    min_volume: float,
) -> str | None:
    if not leg.security_id:
        return "missing security_id - cannot be traded"
    if not leg.ltp.available or leg.ltp.value is None or leg.ltp.value <= 0:
        return "missing or non-positive LTP - cannot be traded"
    if not leg.oi.available or leg.oi.value is None or leg.oi.value < min_oi:
        return f"open interest below minimum ({min_oi:g})"
    if min_volume > 0 and (not leg.volume.available or (leg.volume.value or 0) < min_volume):
        return f"volume below minimum ({min_volume:g})"
    if depth_metrics.spread_pct is None:
        return "spread unavailable (no depth and no bid/ask)"
    if depth_metrics.spread_pct > max_spread_pct:
        return f"spread {depth_metrics.spread_pct:.2f}% exceeds maximum ({max_spread_pct:g}%)"
    if leg.delta.available and leg.delta.value is not None:
        if abs(abs(leg.delta.value) - target_delta) > delta_band:
            return f"delta {leg.delta.value:.2f} outside target band ({target_delta:g} +/- {delta_band:g})"
    return None
