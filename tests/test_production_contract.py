"""Regression test locking in the upstream field-name contract verified
against `artifacts/production_endpoint_samples.json` (real PSYGRID
payloads captured from Oracle on 2026-09-19, market CLOSED at capture
time). This is not a synthetic fixture - it replays the actual bytes
PSYGRID returned, so a future adapter change that silently reverts to a
guessed-but-wrong field name (e.g. flat option legs instead of
strikes/ce/pe nesting) fails here even if every hand-written fixture in
tests/test_snapshot_builder.py still passes.

If PSYGRID's schema changes, this test's assertions - not the schema -
are wrong; update them against a fresh probe, not by loosening them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from psygrid_option_engine.config.settings import EndpointCriticality
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.data.validation import extract_timestamp

_ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "production_endpoint_samples.json"

pytestmark = pytest.mark.skipif(not _ARTIFACT_PATH.exists(), reason="no captured production sample available")


def _bundle_from_artifact() -> RawFetchBundle:
    art = json.loads(_ARTIFACT_PATH.read_text())
    endpoints = art["endpoints"]
    now = datetime.now(UTC)

    mapping = {
        "underlying": "underlying_nifty",
        "options": "options_nifty",
        "depth": "depth_nifty",
        "market_breadth": "market_breadth",
        "sectors": "sectors",
        "global_context": "global_context",
        "india_vix": "india_vix",
    }
    results: dict[str, EndpointFetchResult] = {}
    for logical, key in mapping.items():
        body = endpoints[key].get("body")
        results[logical] = EndpointFetchResult(
            logical_name=logical,
            url="",
            criticality=EndpointCriticality.CRITICAL,
            requested_at=now,
            fetched_at=now,
            latency_ms=1.0,
            http_status=200,
            data=body,
            observed_at=extract_timestamp(body) if body is not None else None,
        )
    return RawFetchBundle(underlying="NIFTY", requested_at=now, results=results)


def test_real_options_chain_parses_to_populated_legs_not_zero() -> None:
    """The original strikes-list-of-{strike,ce,pe} vs. flat-leg guess
    mismatch silently produced zero legs from a real payload - this pins
    the fix."""
    snapshot = build_market_snapshot(_bundle_from_artifact(), as_of=datetime.now(UTC))
    assert snapshot.options is not None
    assert len(snapshot.options.legs) > 0

    leg = snapshot.options.leg(23350, "CE")
    assert leg is not None
    assert leg.ltp.available is True
    assert leg.ltp.value == pytest.approx(88.05)
    assert leg.bid.value == pytest.approx(88.05)
    assert leg.ask.value == pytest.approx(88.55)
    assert leg.delta.available is True
    assert leg.expiry is not None and leg.expiry.isoformat() == "2026-09-22"
    assert leg.oi_change.available is True


def test_real_depth_parses_to_populated_entries_not_zero() -> None:
    """The container key was "contracts" (not in the original alias list)
    and bid/ask were singular, not "bids"/"asks" - this pins the fix."""
    snapshot = build_market_snapshot(_bundle_from_artifact(), as_of=datetime.now(UTC))
    assert snapshot.depth is not None
    assert len(snapshot.depth.by_security_id) > 0


def test_real_global_context_reads_nested_series_not_metadata_keys() -> None:
    """Real series live under a top-level "series" dict; the original
    guess treated the whole payload as a flat name->value map and picked
    up metadata keys instead."""
    snapshot = build_market_snapshot(_bundle_from_artifact(), as_of=datetime.now(UTC))
    names = {s.name for s in snapshot.global_context}
    assert {"sp500", "us_10y_yield", "usd_inr", "vix", "wti_crude_oil"} <= names
    vix_series = next(s for s in snapshot.global_context if s.name == "vix")
    assert vix_series.value.value == pytest.approx(15.44)


def test_real_market_breadth_reads_advancing_declining() -> None:
    """Real keys are "advancing"/"declining" (full words), not the
    originally-guessed "advances"/"declines" abbreviations."""
    snapshot = build_market_snapshot(_bundle_from_artifact(), as_of=datetime.now(UTC))
    assert snapshot.breadth is not None
    assert snapshot.breadth.advances.available is True
    assert snapshot.breadth.declines.available is True
