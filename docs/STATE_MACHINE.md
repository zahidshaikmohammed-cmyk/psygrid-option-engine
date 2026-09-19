# Engine state machine

Implemented in `psygrid_option_engine/api/state_machine.py`. States map
directly to the pipeline layers in `docs/ARCHITECTURE.md`.

## States

| State | Meaning | Implemented? |
|---|---|---|
| `STARTUP` | Process/config initialization. | Yes |
| `DATA_LOADING` | Fetch all configured endpoints for the underlying via `PsygridClient`. | Yes |
| `DATA_VALIDATION` | Structural + freshness validation of fetched payloads; classify critical vs. optional failures. | Yes |
| `WARMING_UP` | Insufficient historical bars/context yet to evaluate structure (e.g. pre-open, first minutes of session). | Stub (Phase 3) |
| `MARKET_ANALYSIS` | Build canonical `MarketSnapshot` from validated raw data. | Stub (Phase 3) |
| `STRUCTURE_ANALYSIS` | Run `structure/` engine over the snapshot. | Stub (Phase 4) |
| `AUTHORIZATION` | Run `authorization/` engine. | Stub (Phase 5) |
| `CONTRACT_SELECTION` | Run `options/` candidate + selection engines. | Stub (Phase 6/7) |
| `TRADE_ENGINEERING` | Run `execution/` engine for entry/SL/TP. | Stub (Phase 8) |
| `RISK_VALIDATION` | Run `risk/` gate. | Stub (Phase 9) |
| `TRADE_READY` | Terminal success state; a `Signal` with `state="TRADE_READY"` is emitted. | Stub (Phase 10) |
| `NO_TRADE` | Terminal state; a `Signal` with `state="NO_TRADE"` and `reasons` is emitted. | Partially — reachable today only from `DATA_VALIDATION` on critical data failure. |
| `INVALIDATED` | A previously `TRADE_READY` signal's structural invalidation has been hit; used by `replay/` and any future live-monitoring loop. | Stub (Phase 11) |
| `SESSION_COMPLETE` | Past the session's trading window; no new decisions will be produced. | Yes (`config/session.py` session-window check) |
| `ERROR` | Unrecoverable engine error (e.g. config invalid, all critical endpoints unreachable after retries). | Yes |

## Transition table

```
STARTUP
  -> DATA_LOADING                 (config validated)
  -> ERROR                        (config invalid)

DATA_LOADING
  -> DATA_VALIDATION              (fetch attempts completed, incl. failures)
  -> ERROR                        (transport-level exception not handled by client retry policy)

DATA_VALIDATION
  -> SESSION_COMPLETE             (current time is outside the trading session window)
  -> NO_TRADE                     (a CRITICAL endpoint is missing, stale beyond
                                    tolerance, or structurally invalid)
  -> WARMING_UP                   (critical data OK, but insufficient history
                                    for structure analysis, e.g. < N bars)
  -> MARKET_ANALYSIS              (critical data OK and sufficient history)

WARMING_UP
  -> MARKET_ANALYSIS              (history requirement now satisfied on a later tick)
  -> SESSION_COMPLETE             (session window elapsed while still warming up)

MARKET_ANALYSIS
  -> STRUCTURE_ANALYSIS           (snapshot built successfully)
  -> NO_TRADE                     (snapshot build itself fails a critical check)

STRUCTURE_ANALYSIS
  -> AUTHORIZATION                (structure evaluated, regardless of bias)

AUTHORIZATION
  -> CONTRACT_SELECTION           (LONG_AUTHORIZED or SHORT_AUTHORIZED)
  -> NO_TRADE                     (NO_AUTHORIZATION)

CONTRACT_SELECTION
  -> TRADE_ENGINEERING            (>= 1 candidate contract survives selection)
  -> NO_TRADE                     (no contract survives liquidity/spread/Greek/
                                    moneyness constraints)

TRADE_ENGINEERING
  -> RISK_VALIDATION              (entry/SL/TP computed)
  -> NO_TRADE                     (no valid premium-level structure derivable,
                                    e.g. invalidation too close to entry)

RISK_VALIDATION
  -> TRADE_READY                  (all risk checks pass)
  -> NO_TRADE                     (any risk check fails; reasons recorded)

TRADE_READY / NO_TRADE / SESSION_COMPLETE / ERROR
  -> (terminal for this decision cycle)
```

A new decision cycle re-enters at `DATA_LOADING` on the next scheduled
tick (see `config/settings.py::DECISION_INTERVAL_SECONDS`). The engine
never mutates a past cycle's emitted `Signal`; `INVALIDATED` is a separate
signal referencing the original one by an id, emitted by a future
monitoring loop (Phase 11), not a rewrite.

## Why `NO_TRADE` is reachable from so many states

Every state past `DATA_VALIDATION` that represents a gate (`AUTHORIZATION`,
`CONTRACT_SELECTION`, `TRADE_ENGINEERING`, `RISK_VALIDATION`) can end the
cycle in `NO_TRADE`. This is intentional and mirrors section 29 of the
brief: the state machine has no path that "forces" a `TRADE_READY` outcome
if any upstream gate says no.
