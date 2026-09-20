from __future__ import annotations

from datetime import UTC, datetime

from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _result(name: str, criticality: EndpointCriticality, data: object) -> EndpointFetchResult:
    return EndpointFetchResult(
        logical_name=name,
        url=f"/public/{name}.json",
        criticality=criticality,
        requested_at=NOW,
        fetched_at=NOW,
        latency_ms=1.0,
        http_status=200,
        data=data,
        observed_at=NOW,
        issues=(),
        error=None,
    )


def _minimal_bundle(**overrides: object) -> RawFetchBundle:
    results = {
        "underlying": _result(
            "underlying",
            EndpointCriticality.CRITICAL,
            {"symbol": "NIFTY", "ltp": 24500.5, "open": 24400, "high": 24550, "low": 24380, "close": 24450},
        ),
        "options": _result("options", EndpointCriticality.CRITICAL, {"data": []}),
        "depth": _result("depth", EndpointCriticality.CRITICAL, {"data": []}),
    }
    results.update(overrides)  # type: ignore[arg-type]
    return RawFetchBundle(underlying="NIFTY", requested_at=NOW, results=results)


def test_missing_underlying_endpoint_yields_none_snapshot() -> None:
    bundle = RawFetchBundle(underlying="NIFTY", requested_at=NOW, results={})
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    assert snap.underlying_snapshot is None
    assert snap.options is None


def test_underlying_field_aliases_resolved() -> None:
    snap = build_market_snapshot(_minimal_bundle(), as_of=NOW, settings=Settings())
    u = snap.underlying_snapshot
    assert u is not None
    assert u.ltp.value == 24500.5
    assert u.day_ohlc.open.value == 24400
    assert u.day_ohlc.high.value == 24550


def test_unknown_field_names_degrade_to_unavailable_not_crash() -> None:
    bundle = _minimal_bundle(
        underlying=_result("underlying", EndpointCriticality.CRITICAL, {"totally_unexpected_shape": True})
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    u = snap.underlying_snapshot
    assert u is not None
    assert u.ltp.available is False
    assert u.day_ohlc.open.available is False


def test_option_leg_parsing_and_type_normalization() -> None:
    # Real payload shape verified against artifacts/production_endpoint_samples.json
    # (2026-09-19): strikes is a list of {strike, ce, pe}, expiry is
    # chain-level, greeks are nested under ce/pe's own "greeks" dict.
    bundle = _minimal_bundle(
        options=_result(
            "options",
            EndpointCriticality.CRITICAL,
            {
                "expiry": "2026-09-25",
                "strikes": [
                    {
                        "strike": 24500,
                        "ce": {
                            "last_price": 120.5,
                            "top_bid_price": 119,
                            "top_ask_price": 121,
                            "greeks": {"delta": 0.55},
                            "security_id": "ABC123",
                        },
                        "pe": {"last_price": 80, "security_id": "ABC124"},
                    },
                ],
            },
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    assert snap.options is not None
    assert len(snap.options.legs) == 2
    ce = snap.options.leg(24500, "CE")
    assert ce is not None
    assert ce.delta.value == 0.55
    assert ce.spread == 2.0
    assert ce.expiry is not None
    assert ce.expiry.isoformat() == "2026-09-25"


def test_option_missing_greeks_marked_unavailable_not_zero() -> None:
    bundle = _minimal_bundle(
        options=_result(
            "options",
            EndpointCriticality.CRITICAL,
            {"expiry": "2026-09-25", "strikes": [{"strike": 24500, "ce": {"last_price": 100}}]},
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    leg = snap.options.leg(24500, "CE")
    assert leg is not None
    assert leg.delta.available is False
    assert leg.delta.value is None


def test_depth_dict_keyed_by_security_id() -> None:
    bundle = _minimal_bundle(
        depth=_result(
            "depth",
            EndpointCriticality.CRITICAL,
            {"ABC123": {"bids": [{"price": 119, "quantity": 75}], "asks": [{"price": 121, "quantity": 50}]}},
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    assert snap.depth is not None
    inst = snap.depth.by_security_id["ABC123"]
    assert inst.bids[0].price == 119
    assert inst.asks[0].quantity == 50


def test_candles_filtered_against_as_of_no_lookahead() -> None:
    bundle = _minimal_bundle(
        underlying=_result(
            "underlying",
            EndpointCriticality.CRITICAL,
            {
                "symbol": "NIFTY",
                "ltp": 100,
                "candles_1m": [
                    {"timestamp": "2026-09-18T04:59:00Z", "open": 1, "high": 2, "low": 0, "close": 1.5},
                    # this one is after `as_of` (NOW) and must be dropped:
                    {"timestamp": "2026-09-18T05:01:00Z", "open": 1, "high": 2, "low": 0, "close": 1.5},
                ],
            },
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    from psygrid_option_engine.domain.timeframe import Timeframe

    m1 = snap.underlying_snapshot.candles.get(Timeframe.M1, ())
    assert len(m1) == 1
    assert m1[0].start < NOW


def test_global_context_preserves_source_date_separately() -> None:
    # Real payload nests series under a top-level "series" key - see
    # artifacts/production_endpoint_samples.json (2026-09-19).
    bundle = _minimal_bundle(
        global_context=_result(
            "global_context",
            EndpointCriticality.OPTIONAL,
            {"series": {"SP500": {"value": 5000, "source_date": "2026-09-17"}}},
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    assert len(snap.global_context) == 1
    series = snap.global_context[0]
    assert series.value.value == 5000
    assert series.source_date.isoformat() == "2026-09-17"


def test_data_quality_propagates_from_bundle() -> None:
    bundle = _minimal_bundle(
        options=EndpointFetchResult(
            logical_name="options",
            url="/public/nifty-options.json",
            criticality=EndpointCriticality.CRITICAL,
            requested_at=NOW,
            fetched_at=None,
            latency_ms=None,
            http_status=None,
            data=None,
            observed_at=None,
            issues=(),
            error="HTTP 503",
        )
    )
    snap = build_market_snapshot(bundle, as_of=NOW, settings=Settings())
    assert snap.data_quality.overall == "INSUFFICIENT"
    assert snap.data_quality.critical_endpoints_ok is False
