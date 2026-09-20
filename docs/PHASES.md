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

**The upstream field-name contract has been verified against a real
production payload as of 2026-09-19** — `artifacts/production_endpoint_samples.json`
was captured from Oracle via `scripts/probe_upstream.py` (market CLOSED at
capture time) and `data/snapshot_builder.py` was corrected against it, with
`tests/test_production_contract.py` replaying the real bytes as a
permanent regression check. This closed several concrete, previously-
guessed-wrong mismatches: the options chain was parsing to **zero legs**
(real shape is `strikes: [{strike, ce, pe}]` with chain-level expiry and
nested `greeks`, not a flat per-leg list), depth was parsing to **zero
entries** (real container key is `contracts`, bid/ask are singular), and
`global_context`/`market_breadth` were reading the wrong keys entirely
(`series` is nested; breadth fields are `advancing`/`declining`, not
`advances`/`declines`). See docs/ENDPOINTS.md's verification-status banner
for the full per-endpoint detail. `futures`, `indicators`, and `rbi_news`
returned HTTP 503 at capture time (no real sample yet) and remain
best-guess/unverified; re-running the Oracle procedure with the market
open is still the way to close that gap. Nothing past the one adapter
module depends on the raw wire format (everything else is built against
the canonical `domain/snapshot.py::MarketSnapshot`), so correcting those
three remaining endpoints later is still a localized fix, not a rewrite.

## What's real vs. what's v1-honest-but-shallow

Every module listed ✅ above is real, tested logic (not a stub) — pure
functions with unit tests, hand-verified indicator math, a genuine
end-to-end test that reaches `TRADE_READY` (not just `NO_TRADE`), and a
replay test that proves live and replay produce byte-identical decisions
from the same snapshot. That said, "done" here means "a solid, honest v1
exists and is exercised by tests" — not "maximally sophisticated." Known
v1 limitations, called out in each module's own docstring:

- **No multi-day history in live mode.** Confirmed by the real payload
  (2026-09-19 sample): the underlying/india_vix endpoints expose only
  intraday candle arrays (`1m`/`5m`/`15m`/`1h`) — there is no daily/weekly
  candle array and no `prev_day`/`prev_week` field at all, not merely an
  unpopulated one. `current_week_high/low` and a genuine daily ATR are
  `None` until `replay/` (or a future live accumulation loop) builds up
  history across sessions.
- **Option-chain "dynamic behaviour"** (how OI shifts as price approaches a
  level) is not implemented — `options/chain.py` is explicitly static-
  snapshot-only; true chain evolution needs the same multi-snapshot history
  as above.
- **Tier thresholds and the execution safety margin are rule-based v1
  constants**, documented as such, not statistically calibrated — that's
  Phase-11-and-beyond `calibration` work once real replay outcomes exist.
- **The strategy framework library covers 8 named frameworks** (trend
  continuation, structured pullback, liquidity-sweep confirmation, range
  rejection, failed-breakout reversal, confirmed structural breakout,
  compression→expansion, structural reversal), not an exhaustive catalogue.
  `structural_reversal` is what fires when `structure/regime.py` classifies
  `MarketRegime.REVERSAL_ATTEMPT` — a regime that previously had zero
  framework coverage, meaning a genuine reversal-attempt read could never
  become an actionable opportunity; it now requires a REJECTION reaction at
  a *major* structural level (PDH/PDL/PWH/PWL/session high/low), not any
  minor liquidity zone.
- **`--live` mode now monitors an active `TRADE_READY` signal tick-to-tick**
  (`run_engine.py::_check_active_trades`): each cycle it checks the
  underlying's current LTP against the structural invalidation level and
  looks up the selected contract's current premium (from that same tick's
  already-fetched data, no extra request) against the stop/target, and
  calls `LifecycleTracker.update(..., invalidated=True/targeted=True)`
  accordingly. This lives in `run_engine.py`, not `api/decision.py` —
  tick-to-tick position monitoring is live-loop bookkeeping, deliberately
  kept out of the stateless decision core.

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
