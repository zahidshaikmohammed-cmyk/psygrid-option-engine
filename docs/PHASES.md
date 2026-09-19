# Implementation phases

Status legend: ✅ done · 🚧 in progress · ⬜ not started.

| Phase | Scope | Status |
|---|---|---|
| 0 | Inspect repo, produce architecture/contract/schema/state-machine/testing plan | ✅ |
| 1 | Project skeleton, configuration, core schemas (`SourcedField`, `Signal`) | ✅ |
| 2 | PSYGRID upstream client + response/schema/freshness validation | ✅ |
| 3 | Canonical `MarketSnapshot` + timeframe aggregation | ✅ |
| 4 | Market structure engine (regime, swings, levels, liquidity, momentum, pullback, volatility, futures, breadth, divergence, context) | ✅ |
| 5 | Authorization engine (confluence, tiers, strategy frameworks, opportunity generation) | ✅ |
| 6 | Option candidate engine (chain metrics, depth microstructure) | ✅ |
| 7 | Contract selection | ✅ |
| 8 | Premium trade engineering (entry/SL/TP) | ✅ |
| 9 | Risk validation | ✅ |
| 10 | Signal output + runtime (full state machine), `run_engine.py` CLI | ✅ |
| 11 | Replay infrastructure using the live decision function | 🚧 (see below) |

## Verification status — read before trusting any output

**The upstream field-name contract used throughout (`data/snapshot_builder.py`'s
alias tables) is still unverified against a real production payload** — see
docs/ENDPOINTS.md. Nothing past that one adapter module depends on the raw
wire format (everything else is built against the canonical
`domain/snapshot.py::MarketSnapshot`), so correcting the contract later is a
localized fix, not a rewrite — but until that correction happens, treat any
live run against the real upstream as unverified. `scripts/probe_upstream.py`
+ the Oracle procedure in docs/ENDPOINTS.md is still the way to close this
gap; nobody has run it yet.

## What's real vs. what's v1-honest-but-shallow

Every module listed ✅ above is real, tested logic (not a stub) — pure
functions with unit tests, hand-verified indicator math, a genuine
end-to-end test that reaches `TRADE_READY` (not just `NO_TRADE`), and a
replay test that proves live and replay produce byte-identical decisions
from the same snapshot. That said, "done" here means "a solid, honest v1
exists and is exercised by tests" — not "maximally sophisticated." Known
v1 limitations, called out in each module's own docstring:

- **No multi-day history in live mode.** A single snapshot fetch typically
  only has today's M1 candles plus whatever `prev_day`/`prev_week` fields
  the underlying endpoint exposes directly. `current_week_high/low` and a
  genuine daily ATR are `None` until `replay/` (or a future live
  accumulation loop) builds up history across sessions.
- **Option-chain "dynamic behaviour"** (how OI shifts as price approaches a
  level) is not implemented — `options/chain.py` is explicitly static-
  snapshot-only; true chain evolution needs the same multi-snapshot history
  as above.
- **Tier thresholds and the execution safety margin are rule-based v1
  constants**, documented as such, not statistically calibrated — that's
  Phase-11-and-beyond `calibration` work once real replay outcomes exist.
- **The strategy framework library covers 7 named frameworks** (trend
  continuation, structured pullback, liquidity-sweep confirmation, range
  rejection, failed-breakout reversal, confirmed structural breakout,
  compression→expansion), not an exhaustive catalogue.
- **`--live` mode's lifecycle dedup doesn't yet detect invalidation/target
  hits between ticks** (it never calls `LifecycleTracker.update(...,
  invalidated=True/targeted=True)`) — it correctly avoids re-printing an
  unchanged setup, but doesn't yet actively monitor an active trade's
  outcome tick-to-tick.

None of this is fabricated data or a fake pass-through — every gap above
is a documented "not yet" with an honest fallback (usually `None`/
`UNAVAILABLE`), never a guess dressed up as a real value.

## Replay (Phase 11) — what exists, what's next

`replay/snapshot_store.py` (JSONL capture/replay of `RawFetchBundle`s) and
`replay/engine.py` (`replay()`, which runs bundles through the exact same
`api/decision.py::decide` the live runtime uses, strictly in chronological
order) are built and tested, including a test that proves a later bundle in
the same replay batch cannot change an earlier bundle's signal. What's not
yet built:

- A capture loop that actually calls `PsygridClient.fetch_snapshot` on an
  interval and appends to the store (straightforward — `EngineRuntime`
  already does the fetch half; it would just add an `append_bundle` call).
- Outcome measurement (R-multiple, MAE/MFE, performance by regime/tier/
  strategy/option type, expected-range accuracy) — brief section 32/33.
  This needs captured history to exist first.
- `calibration` — revisiting tier thresholds and the execution safety
  margin against measured replay outcomes, per brief section 33's explicit
  rule against presenting uncalibrated numbers as validated probabilities.

## Running it

```bash
python run_engine.py --once [--underlying NIFTY|BANKNIFTY|BOTH]
python run_engine.py --live [--interval SECONDS]
```

Signal-only — see docs/SAFETY.md. `--once` performs a full intelligence
cycle and prints a human-readable report plus the current best opportunity;
`--live` repeats that on an interval, printing only when a tracked setup's
state actually changes.
