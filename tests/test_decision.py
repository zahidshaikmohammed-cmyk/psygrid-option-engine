from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.api.decision import decide
from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot

SESSION_OPEN = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST, Friday


def _result(name: str, criticality: EndpointCriticality, data: object, *, as_of: datetime) -> EndpointFetchResult:
    return EndpointFetchResult(
        logical_name=name, url=f"/public/{name}.json", criticality=criticality, requested_at=as_of,
        fetched_at=as_of, latency_ms=1.0, http_status=200, data=data, observed_at=as_of, issues=(), error=None,
    )


def _uptrend_candles(n: int) -> list[dict]:
    """10-bar cycle (5 up @ +15, 5 shallow pullback @ -8): clean HH/HL, and
    with n=85 the final 5-bar momentum window (indices 80-84) sits entirely
    within one up-leg, so the deterministic result is TRENDING_UP with
    aligned STRONG momentum, not dependent on random data."""
    candles = []
    price = 24000.0
    for i in range(n):
        cycle_pos = i % 10
        price += 15 if cycle_pos < 5 else -8
        ts = (SESSION_OPEN + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        candles.append(
            {"timestamp": ts, "open": price, "high": price + 2, "low": price - 2, "close": price, "volume": 2000}
        )
    return candles


def test_critical_data_missing_yields_no_trade() -> None:
    as_of = SESSION_OPEN + timedelta(minutes=30)
    bundle = RawFetchBundle(underlying="NIFTY", requested_at=as_of, results={})
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())
    signal = decide(snapshot, settings=Settings())
    assert signal.state == "NO_TRADE"
    assert signal.tier == 0
    assert signal.reasons


def test_missing_ltp_yields_no_trade() -> None:
    as_of = SESSION_OPEN + timedelta(minutes=30)
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result("underlying", EndpointCriticality.CRITICAL, {"open": 24000}, as_of=as_of),
            "options": _result("options", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
            "depth": _result("depth", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
        },
    )
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())
    signal = decide(snapshot, settings=Settings())
    assert signal.state == "NO_TRADE"
    assert "last traded price" in signal.reasons[0]


def test_flat_market_no_evidence_yields_no_trade_with_developing_setup_absent_or_tier_zero() -> None:
    as_of = SESSION_OPEN + timedelta(minutes=5)
    flat = [
        {
            "timestamp": (SESSION_OPEN + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
            "open": 24000, "high": 24001, "low": 23999, "close": 24000, "volume": 100,
        }
        for i in range(5)
    ]
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result(
                "underlying", EndpointCriticality.CRITICAL,
                {"symbol": "NIFTY", "ltp": 24000, "open": 24000, "high": 24001, "low": 23999,
                 "close": 24000, "candles_1m": flat},
                as_of=as_of,
            ),
            "options": _result("options", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
            "depth": _result("depth", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
        },
    )
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())
    signal = decide(snapshot, settings=Settings())
    assert signal.state == "NO_TRADE"
    assert signal.tier == 0


def test_favorable_conditions_reach_trade_ready() -> None:
    candles = _uptrend_candles(85)
    as_of = SESSION_OPEN + timedelta(minutes=85)
    ltp = candles[-1]["close"]
    strike = round(ltp / 50) * 50

    # Real shape verified against artifacts/production_endpoint_samples.json
    # (2026-09-19): strikes list of {strike, ce, pe}, chain-level expiry,
    # greeks nested under ce/pe's own "greeks" dict.
    options_data = {
        "expiry": "2026-09-25",
        "strikes": [
            {
                "strike": strike,
                "ce": {
                    "last_price": 120.0, "top_bid_price": 119.5, "top_ask_price": 120.5,
                    "oi": 80000, "volume": 30000, "greeks": {"delta": 0.5}, "security_id": "CE1",
                },
                "pe": {
                    "last_price": 90.0, "top_bid_price": 89.5, "top_ask_price": 90.5,
                    "oi": 20000, "volume": 5000, "greeks": {"delta": -0.5}, "security_id": "PE1",
                },
            },
        ],
    }
    depth_data = {
        "contracts": [
            {"security_id": "CE1",
             "bid": [{"price": 119.5, "quantity": 1000}], "ask": [{"price": 120.5, "quantity": 1000}]},
            {"security_id": "PE1",
             "bid": [{"price": 89.5, "quantity": 1000}], "ask": [{"price": 90.5, "quantity": 1000}]},
        ]
    }

    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result(
                "underlying", EndpointCriticality.CRITICAL,
                {
                    "symbol": "NIFTY", "ltp": ltp, "open": candles[0]["open"],
                    "high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles),
                    "close": ltp, "candles_1m": candles,
                },
                as_of=as_of,
            ),
            "options": _result("options", EndpointCriticality.CRITICAL, options_data, as_of=as_of),
            "depth": _result("depth", EndpointCriticality.CRITICAL, depth_data, as_of=as_of),
            "india_vix": _result("india_vix", EndpointCriticality.OPTIONAL, {"value": 18.0}, as_of=as_of),
            "market_breadth": _result(
                "market_breadth", EndpointCriticality.OPTIONAL, {"advances": 1800, "declines": 400}, as_of=as_of
            ),
            "futures": _result(
                "futures", EndpointCriticality.OPTIONAL,
                {"expiry": "2026-09-25", "ltp": ltp + 20, "oi": 200000, "oi_change": 20000}, as_of=as_of,
            ),
        },
    )
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())
    signal = decide(snapshot, settings=Settings())

    assert signal.state == "TRADE_READY", signal.reasons
    assert signal.direction == "CALL"
    assert signal.tier >= 1
    assert signal.contract.option_type == "CE"
    assert signal.execution.stop_loss < signal.execution.entry < signal.execution.take_profit
    assert signal.execution.risk_reward > 0
    assert signal.reasons


def test_session_cutoff_blocks_new_trade_even_with_favorable_setup() -> None:
    candles = _uptrend_candles(85)
    # 15:15 IST -> past the 15:00 entry cutoff but still "within session"
    as_of = datetime(2026, 9, 18, 9, 45, tzinfo=UTC)
    ltp = candles[-1]["close"]
    # An extreme VIX is used here purely to overcome how little session time
    # remains at 15:15 IST (the expected-range model scales reward by
    # remaining time) so the setup still clears every OTHER tier/risk gate,
    # isolating the entry-cutoff check as the one thing that blocks it.

    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result(
                "underlying", EndpointCriticality.CRITICAL,
                {
                    "symbol": "NIFTY", "ltp": ltp, "open": candles[0]["open"],
                    "high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles),
                    "close": ltp, "candles_1m": candles,
                },
                as_of=as_of,
            ),
            "options": _result(
                "options", EndpointCriticality.CRITICAL,
                {"expiry": "2026-09-25", "strikes": [
                    {"strike": round(ltp / 50) * 50, "ce": {
                        "last_price": 120.0, "top_bid_price": 119.5, "top_ask_price": 120.5,
                        "oi": 80000, "volume": 30000, "greeks": {"delta": 0.5}, "security_id": "CE1",
                    }},
                ]},
                as_of=as_of,
            ),
            "depth": _result(
                "depth", EndpointCriticality.CRITICAL,
                {"contracts": [{"security_id": "CE1", "bid": [{"price": 119.5, "quantity": 1000}],
                                "ask": [{"price": 120.5, "quantity": 1000}]}]},
                as_of=as_of,
            ),
            "india_vix": _result("india_vix", EndpointCriticality.OPTIONAL, {"value": 80.0}, as_of=as_of),
            "market_breadth": _result(
                "market_breadth", EndpointCriticality.OPTIONAL, {"advances": 1800, "declines": 400}, as_of=as_of
            ),
            "futures": _result(
                "futures", EndpointCriticality.OPTIONAL,
                {"expiry": "2026-09-25", "ltp": ltp + 20, "oi": 200000, "oi_change": 20000}, as_of=as_of,
            ),
        },
    )
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())
    signal = decide(snapshot, settings=Settings())
    assert signal.state == "NO_TRADE"
    assert any("session" in r.lower() or "cutoff" in r.lower() for r in signal.reasons)
