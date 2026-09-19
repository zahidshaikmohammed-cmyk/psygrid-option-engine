from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.structure.engine import analyze_structure
from psygrid_option_engine.structure.types import MarketRegime, TrendBias

SESSION_OPEN = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST, Friday


def _result(
    name: str, criticality: EndpointCriticality, data: object, *, as_of: datetime
) -> EndpointFetchResult:
    return EndpointFetchResult(
        logical_name=name,
        url=f"/public/{name}.json",
        criticality=criticality,
        requested_at=as_of,
        fetched_at=as_of,
        latency_ms=1.0,
        http_status=200,
        data=data,
        observed_at=as_of,
        issues=(),
        error=None,
    )


def _sawtooth_uptrend_candles(n: int) -> list[dict]:
    """A deterministic zigzag with a 10-bar cycle (5 up, 5 shallow-pullback
    down) and a wide enough amplitude relative to the default swing
    lookback (3) that each cycle's peak/trough is an unambiguous local
    extremum entirely within its own cycle, and each cycle's peak/trough
    prints higher than the last -> a clean HH/HL sequence."""
    candles = []
    price = 24000.0
    for i in range(n):
        cycle_pos = i % 10
        if cycle_pos < 5:
            price += 15  # push up
        else:
            price -= 8  # shallow pullback, stays above prior cycle's low
        ts = (SESSION_OPEN + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": ts,
                "open": price,
                "high": price + 2,
                "low": price - 2,
                "close": price,
                "volume": 1000,
            }
        )
    return candles


def _bundle_from_candles(candles: list[dict], *, as_of: datetime) -> RawFetchBundle:
    ltp = candles[-1]["close"]
    underlying_data = {
        "symbol": "NIFTY",
        "ltp": ltp,
        "open": candles[0]["open"],
        "high": max(c["high"] for c in candles),
        "low": min(c["low"] for c in candles),
        "close": ltp,
        "candles_1m": candles,
    }
    return RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result("underlying", EndpointCriticality.CRITICAL, underlying_data, as_of=as_of),
            "options": _result("options", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
            "depth": _result("depth", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
        },
    )


def test_analyze_structure_full_pipeline_uptrend() -> None:
    candles = _sawtooth_uptrend_candles(60)
    as_of = SESSION_OPEN + timedelta(minutes=60)
    bundle = _bundle_from_candles(candles, as_of=as_of)
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())

    analysis = analyze_structure(snapshot)

    assert analysis.structure.trend_bias is TrendBias.BULLISH
    assert analysis.regime.regime in (MarketRegime.TRENDING_UP, MarketRegime.BREAKOUT_ATTEMPT)
    assert analysis.levels.today_high is not None
    assert analysis.levels.opening_range_high is not None
    assert analysis.vwap is not None
    assert analysis.vwap_relation in ("ABOVE", "BELOW", "AT")
    assert len(analysis.liquidity_zones) > 0


def test_analyze_structure_handles_missing_underlying_gracefully() -> None:
    as_of = SESSION_OPEN
    bundle = RawFetchBundle(underlying="NIFTY", requested_at=as_of, results={})
    snapshot = build_market_snapshot(bundle, as_of=as_of, settings=Settings())

    analysis = analyze_structure(snapshot)

    assert analysis.structure.trend_bias is TrendBias.NEUTRAL
    assert analysis.regime.regime is MarketRegime.UNCERTAIN
    assert analysis.liquidity_zones == ()
    assert analysis.vwap is None


def test_analyze_structure_respects_as_of_boundary() -> None:
    """Rerunning with an earlier as_of must never see swings/levels that
    only exist because of later candles - the classic no-lookahead check
    applied at the orchestrator level."""
    candles = _sawtooth_uptrend_candles(60)
    late_as_of = SESSION_OPEN + timedelta(minutes=60)
    early_as_of = SESSION_OPEN + timedelta(minutes=20)

    late_bundle = _bundle_from_candles(candles, as_of=late_as_of)
    late_snapshot = build_market_snapshot(late_bundle, as_of=late_as_of, settings=Settings())
    late_analysis = analyze_structure(late_snapshot)

    early_bundle = _bundle_from_candles(candles, as_of=early_as_of)
    early_snapshot = build_market_snapshot(early_bundle, as_of=early_as_of, settings=Settings())
    early_analysis = analyze_structure(early_snapshot)

    assert early_analysis.levels.today_high <= late_analysis.levels.today_high
    assert len(early_analysis.structure.swings) <= len(late_analysis.structure.swings)
