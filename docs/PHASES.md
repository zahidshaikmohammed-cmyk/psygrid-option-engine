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
| 12 | Production hardening pass (data-quality fail-closed, fabrication audit, session/lifecycle correctness) | ✅ (see below) |

## Production hardening (2026-09-20) — what changed and why

A pass driven directly by the real captured Oracle payload and a
systematic audit against the "no fabrication, fail closed" design rule
already stated throughout this codebase, catching several places where
that rule wasn't actually being enforced end to end:

- **`critical_endpoints_ok` now means what it says.** Previously it only
  checked raw HTTP/JSON fetch success — a critical endpoint that was
  stale beyond tolerance, structurally invalid, future-dated relative to
  the decision's own `as_of` (an information-boundary violation), marked
  synthetic/placeholder by PSYGRID itself (`synthetic_data`/
  `synthetic_candles: true`), or that fetched fine but extracted to
  nothing usable (e.g. zero option legs) all still reported
  `critical_endpoints_ok = True`. All five now correctly force it `False`
  → `NO_TRADE`. See `data/validation.py::assess_source_status`/
  `build_data_quality`.
- **A real, valid depth payload no longer gets a false structural-issue
  warning.** `data/validation.py` and `data/snapshot_builder.py` had two
  separately-maintained "recognizable list container key" lists that had
  drifted apart — the validation copy was missing `"contracts"` (the
  depth endpoint's real container key), so a genuinely healthy payload
  was flagged as suspect. Now one shared list (`LIST_CONTAINER_KEYS`).
- **Depth microstructure no longer reports a fabricated
  `best_bid=0.0`/`best_ask=0.0`.** The real depth payload sends a
  fixed-length 20-slot array per side even when nothing is quoted
  (`{"price": 0.0, "quantity": 0}` per empty slot) — these are filtered
  out at the adapter boundary so an all-empty ladder correctly becomes
  "no real depth" (falls back to the option chain's own bid/ask) instead
  of a hollow object masquerading as a real one.
- **Session/lifecycle correctness.** The 15:00 IST entry cutoff now also
  force-exits any internally `ACTIVE` trade still being monitored in
  `--live` mode (it previously only blocked *new* entries — an already-
  active one could keep being reported live indefinitely). A resolved
  (invalidated/targeted) setup no longer immediately resurrects on the
  next tick: setup identity now includes the numeric structural
  invalidation level, and a configurable cooldown
  (`PSYGRID_LIFECYCLE_REENTRY_COOLDOWN_SECONDS`, default 300s) is a
  secondary guard against a same-level whipsaw. See
  `signals/lifecycle.py` and `run_engine.py::_force_session_exit`/
  `_register_new_active_trades`.
- **Two fully-implemented, fully-tested evidence modules were computed
  and never used.** `market/context.py::analyze_context` (macro/news
  context, event-risk flagging) and `market/divergence.py::
  detect_divergences` (breadth/volume/momentum/futures divergence
  detection) existed with their own test suites but were never called
  from `api/decision.py` — their output reached nowhere. Both are now
  wired into the per-direction evidence stream.
- **The already-computed expected-range model was thrown away before it
  reached output.** `market_state.expected_range_upper/lower` were
  hardcoded `None` despite `market/volatility.py::RangeModel` being
  computed every cycle; now exposed along with its ATR/VIX components,
  source, session range, and remaining range.
- **`price_change_pct` used a raw field the real payload never sends**
  (confirmed absent, not just unpopulated) instead of the canonical
  M1-derived session open `structure/levels.py` already computes.
- **A fabricated "weekly range."** When no multi-day D1 history existed
  (the normal case for a single live fetch), `current_week_high/low` was
  silently computed from *today's own* M1 candles — relabeling "today's
  range" as "this week's range." Now genuinely `None` (exposed as
  `week_range_status: "INSUFFICIENT_HISTORY"`) unless real multi-day
  history is present.
- Also: NaN/Infinite values (Python's `json` module accepts these
  non-standard literals by default) are now rejected as unavailable
  rather than silently corrupting downstream arithmetic; duplicate/
  out-of-order upstream candles are deduped (last occurrence wins) and
  sorted; missing/non-positive option LTP is now a hard rejection in
  contract selection, matching the existing missing-`security_id` one.

See `tests/test_validation.py`, `tests/test_snapshot_builder.py`,
`tests/test_options_selection.py`, `tests/test_signal_lifecycle.py`, and
`tests/test_run_engine.py` for the regression tests locking each of these
in (14:59:59/15:00:00/15:00:01/15:15:00 IST session-boundary tests among
them), and `tests/test_replay.py::test_live_and_replay_produce_the_same_signal_from_the_same_bundle`
for an explicit, executable proof of the live/replay parity claim below
(not just an architectural assertion).

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
- **`--live` mode monitors an active `TRADE_READY` signal tick-to-tick**
  (`run_engine.py::_check_active_trades`): each cycle it checks the
  underlying's current LTP against the structural invalidation level and
  looks up the selected contract's current premium (from that same tick's
  already-fetched data, no extra request) against the stop/target, and
  calls `LifecycleTracker.update(..., invalidated=True/targeted=True)`
  accordingly. At/after the 15:00 IST entry cutoff, `_force_session_exit`
  additionally transitions any still-active trade to the `EXPIRED`
  terminal state regardless of whether it hit stop/target/invalidation.
  This all lives in `run_engine.py`, not `api/decision.py` — tick-to-tick
  position monitoring is live-loop bookkeeping, deliberately kept out of
  the stateless decision core.

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
python run_engine.py --once [--underlying NIFTY|BANKNIFTY|SENSEX|ALL]
python run_engine.py --live [--interval SECONDS]
```

Signal-only — see docs/SAFETY.md. `--once` performs a full intelligence
cycle and prints a human-readable report plus the current best opportunity;
`--live` repeats that on an interval, printing only when a tracked setup's
state actually changes.
