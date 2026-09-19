"""Deterministic replay driver (brief section 32): "LIVE and REPLAY must
share the same decision engine."

`replay()` calls the exact same `api/decision.py::decide` function the
live runtime calls, on snapshots built by the exact same
`data/snapshot_builder.py::build_market_snapshot`. There is no separate
"backtest-only" decision path. Bundles are processed strictly in
`requested_at` order; each snapshot's `as_of` is that bundle's own capture
time, so `decide()`'s no-lookahead guarantees (docs/ARCHITECTURE.md
section 3) apply identically to replay and live.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psygrid_option_engine.api.decision import decide
from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.models import RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.signals.schema import Signal


@dataclass(frozen=True)
class ReplayResult:
    as_of: datetime
    signal: Signal


def replay(bundles: Sequence[RawFetchBundle], *, settings: Settings | None = None) -> list[ReplayResult]:
    settings = settings or get_settings()
    ordered = sorted(bundles, key=lambda b: b.requested_at)

    results: list[ReplayResult] = []
    for bundle in ordered:
        as_of = bundle.requested_at
        snapshot = build_market_snapshot(bundle, as_of=as_of, settings=settings)
        signal = decide(snapshot, settings=settings)
        results.append(ReplayResult(as_of=as_of, signal=signal))
    return results
