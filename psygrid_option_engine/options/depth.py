"""Option market-depth / microstructure evidence (brief section 15).

A directionally good setup can still have a poor tradable contract - this
module answers "can this contract actually be executed well?", completely
independent of whether the underlying thesis is sound.

`GOOD`/`FAIR`/`POOR` thresholds are named parameters with documented
defaults, not buried magic numbers - they are heuristic starting points
pending calibration against real execution data (brief section 33: not
presented as statistically validated), and callers can override them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psygrid_option_engine.domain.snapshot import InstrumentDepth

LiquidityQuality = Literal["GOOD", "FAIR", "POOR", "UNKNOWN"]


@dataclass(frozen=True)
class DepthMetrics:
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    spread_pct: float | None
    bid_ask_imbalance: float | None  # best-level (bid_qty - ask_qty) / (bid_qty + ask_qty)
    depth_imbalance: float | None  # same, summed across all captured levels
    total_bid_qty: float | None
    total_ask_qty: float | None
    liquidity_quality: LiquidityQuality


def compute_depth_metrics(
    depth: InstrumentDepth | None,
    *,
    reference_price: float | None = None,
    good_spread_pct: float = 1.0,
    good_min_qty: float = 300.0,
    fair_spread_pct: float = 3.0,
    fair_min_qty: float = 75.0,
) -> DepthMetrics:
    if depth is None or (not depth.bids and not depth.asks):
        return DepthMetrics(None, None, None, None, None, None, None, None, "UNKNOWN")

    best_bid = depth.bids[0].price if depth.bids else None
    best_ask = depth.asks[0].price if depth.asks else None

    spread: float | None = None
    mid: float | None = reference_price
    if best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2

    spread_pct = (spread / mid * 100) if (spread is not None and mid) else None

    best_bid_qty = depth.bids[0].quantity if depth.bids else None
    best_ask_qty = depth.asks[0].quantity if depth.asks else None
    bid_ask_imbalance = _imbalance(best_bid_qty, best_ask_qty)

    total_bid_qty = sum(level.quantity for level in depth.bids) if depth.bids else None
    total_ask_qty = sum(level.quantity for level in depth.asks) if depth.asks else None
    depth_imbalance = _imbalance(total_bid_qty, total_ask_qty)

    liquidity_quality: LiquidityQuality
    if spread_pct is None or total_bid_qty is None or total_ask_qty is None:
        liquidity_quality = "UNKNOWN"
    else:
        min_qty = min(total_bid_qty, total_ask_qty)
        if spread_pct <= good_spread_pct and min_qty >= good_min_qty:
            liquidity_quality = "GOOD"
        elif spread_pct <= fair_spread_pct and min_qty >= fair_min_qty:
            liquidity_quality = "FAIR"
        else:
            liquidity_quality = "POOR"

    return DepthMetrics(
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        spread_pct=spread_pct,
        bid_ask_imbalance=bid_ask_imbalance,
        depth_imbalance=depth_imbalance,
        total_bid_qty=total_bid_qty,
        total_ask_qty=total_ask_qty,
        liquidity_quality=liquidity_quality,
    )


def _imbalance(bid_qty: float | None, ask_qty: float | None) -> float | None:
    if bid_qty is None or ask_qty is None or (bid_qty + ask_qty) <= 0:
        return None
    return (bid_qty - ask_qty) / (bid_qty + ask_qty)
