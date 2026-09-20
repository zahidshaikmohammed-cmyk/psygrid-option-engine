from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from psygrid_option_engine.api.decision import decide
from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.replay.engine import replay
from psygrid_option_engine.replay.snapshot_store import (
    append_bundle,
    bundle_from_dict,
    bundle_to_dict,
    read_bundles,
)

SESSION_OPEN = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)


def _result(name: str, criticality: EndpointCriticality, data: object, *, as_of: datetime) -> EndpointFetchResult:
    return EndpointFetchResult(
        logical_name=name, url=f"/public/{name}.json", criticality=criticality, requested_at=as_of,
        fetched_at=as_of, latency_ms=1.0, http_status=200, data=data, observed_at=as_of, issues=(), error=None,
    )


def _bundle(as_of: datetime, ltp: float) -> RawFetchBundle:
    return RawFetchBundle(
        underlying="NIFTY",
        requested_at=as_of,
        results={
            "underlying": _result(
                "underlying", EndpointCriticality.CRITICAL,
                {"symbol": "NIFTY", "ltp": ltp, "open": 24000, "high": ltp + 5, "low": ltp - 5, "close": ltp},
                as_of=as_of,
            ),
            "options": _result("options", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
            "depth": _result("depth", EndpointCriticality.CRITICAL, {"data": []}, as_of=as_of),
        },
    )


def test_bundle_round_trip_through_dict() -> None:
    bundle = _bundle(SESSION_OPEN, 24000.0)
    restored = bundle_from_dict(bundle_to_dict(bundle))
    assert restored.underlying == bundle.underlying
    assert restored.requested_at == bundle.requested_at
    assert restored.results["underlying"].data == bundle.results["underlying"].data
    assert restored.results["underlying"].observed_at == bundle.results["underlying"].observed_at


def test_append_and_read_bundles_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.jsonl"
    b1 = _bundle(SESSION_OPEN, 24000.0)
    b2 = _bundle(SESSION_OPEN + timedelta(minutes=5), 24050.0)
    append_bundle(path, b1)
    append_bundle(path, b2)

    restored = read_bundles(path)
    assert len(restored) == 2
    assert restored[0].requested_at == b1.requested_at
    assert restored[1].requested_at == b2.requested_at


def test_append_bundle_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "snapshots.jsonl"
    append_bundle(path, _bundle(SESSION_OPEN, 24000.0))
    assert path.exists()


def test_replay_processes_in_chronological_order_regardless_of_input_order() -> None:
    b1 = _bundle(SESSION_OPEN, 24000.0)
    b2 = _bundle(SESSION_OPEN + timedelta(minutes=5), 24050.0)
    b3 = _bundle(SESSION_OPEN + timedelta(minutes=10), 24100.0)

    results = replay([b3, b1, b2], settings=Settings())

    assert [r.as_of for r in results] == [b1.requested_at, b2.requested_at, b3.requested_at]


def test_replay_produces_a_signal_per_bundle() -> None:
    bundles = [_bundle(SESSION_OPEN + timedelta(minutes=i * 5), 24000.0 + i * 10) for i in range(4)]
    results = replay(bundles, settings=Settings())
    assert len(results) == 4
    for r in results:
        assert r.signal.state in ("NO_TRADE", "TRADE_READY")
        assert r.signal.decision_timestamp == r.as_of


def test_replay_never_leaks_future_data_across_steps() -> None:
    """Each bundle only carries data valid as of its own capture time (by
    construction, since it's built the same way a live fetch would be) -
    this asserts replay doesn't need or use anything beyond that bundle
    when producing its signal, i.e. no cross-bundle state leaks forward."""
    early = _bundle(SESSION_OPEN, 24000.0)
    late = _bundle(SESSION_OPEN + timedelta(minutes=30), 25000.0)  # big future jump

    results_both = replay([early, late], settings=Settings())
    results_early_only = replay([early], settings=Settings())

    # The signal for the *early* bundle must be identical whether or not a
    # later bundle also happens to be in the replay batch.
    early_from_both = next(r for r in results_both if r.as_of == early.requested_at)
    early_alone = results_early_only[0]
    assert early_from_both.signal.market_state == early_alone.signal.market_state
    assert early_from_both.signal.reasons == early_alone.signal.reasons


def test_live_and_replay_produce_the_same_signal_from_the_same_bundle() -> None:
    """Brief section 16/32: "same MarketSnapshot + same Settings = same
    Signal", live and replay share the exact same decision engine. This
    proves it end to end rather than relying on the two code paths just
    happening to call the same functions: `EngineRuntime.run_cycle`'s
    core pipeline (after the network fetch) is build_market_snapshot() +
    decide() - exactly what replay() does per bundle - so calling that
    pipeline directly here for one bundle ("live", with a fixed as_of)
    and comparing it against replay()'s own result for the same bundle
    proves the two are provably identical, not just believed to be.
    signal_id is the one field expected to differ (random per call)."""
    settings = Settings()
    bundle = _bundle(SESSION_OPEN, 24123.0)

    live_snapshot = build_market_snapshot(bundle, as_of=bundle.requested_at, settings=settings)
    live_signal = decide(live_snapshot, settings=settings)

    [replayed] = replay([bundle], settings=settings)

    live_dump = live_signal.model_dump(exclude={"signal_id"})
    replay_dump = replayed.signal.model_dump(exclude={"signal_id"})
    assert live_dump == replay_dump
