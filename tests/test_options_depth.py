from __future__ import annotations

import pytest

from psygrid_option_engine.domain.snapshot import DepthLevel, InstrumentDepth
from psygrid_option_engine.options.depth import compute_depth_metrics


def test_no_depth_is_unknown() -> None:
    metrics = compute_depth_metrics(None)
    assert metrics.liquidity_quality == "UNKNOWN"


def test_spread_and_mid_computed() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=99.0, quantity=300),),
        asks=(DepthLevel(price=101.0, quantity=300),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.spread == pytest.approx(2.0)
    assert metrics.spread_pct == pytest.approx(2 / 100 * 100)


def test_good_liquidity_classification() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=99.5, quantity=500),),
        asks=(DepthLevel(price=100.5, quantity=500),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.liquidity_quality == "GOOD"


def test_poor_liquidity_wide_spread() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=80.0, quantity=500),),
        asks=(DepthLevel(price=120.0, quantity=500),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.liquidity_quality == "POOR"


def test_poor_liquidity_thin_depth() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=99.9, quantity=10),),
        asks=(DepthLevel(price=100.1, quantity=10),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.liquidity_quality == "POOR"


def test_bid_ask_imbalance_signed_correctly() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=99.5, quantity=300),),
        asks=(DepthLevel(price=100.5, quantity=100),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.bid_ask_imbalance == pytest.approx((300 - 100) / 400)


def test_depth_imbalance_sums_all_levels() -> None:
    depth = InstrumentDepth(
        security_id="x",
        bids=(DepthLevel(price=99.5, quantity=100), DepthLevel(price=99.0, quantity=100)),
        asks=(DepthLevel(price=100.5, quantity=50),),
    )
    metrics = compute_depth_metrics(depth)
    assert metrics.total_bid_qty == 200
    assert metrics.total_ask_qty == 50
    assert metrics.depth_imbalance == pytest.approx((200 - 50) / 250)


def test_one_sided_depth_no_spread() -> None:
    depth = InstrumentDepth(security_id="x", bids=(DepthLevel(price=99.0, quantity=100),), asks=())
    metrics = compute_depth_metrics(depth, reference_price=100.0)
    assert metrics.spread is None
    assert metrics.liquidity_quality == "UNKNOWN"
