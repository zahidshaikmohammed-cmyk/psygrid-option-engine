# Implementation phases

Status legend: ✅ done · 🚧 in progress · ⬜ not started (package/docstring
skeleton only).

| Phase | Scope | Status |
|---|---|---|
| 0 | Inspect repo, produce architecture/contract/schema/state-machine/testing plan | ✅ |
| 1 | Project skeleton, configuration, core schemas (`SourcedField`, `Signal`) | ✅ |
| 2 | PSYGRID upstream client + response/schema/freshness validation | ✅ |
| 3 | Canonical `MarketSnapshot` + timeframe aggregation | ⬜ |
| 4 | Market structure engine | ⬜ |
| 5 | Authorization engine | ⬜ |
| 6 | Option candidate engine | ⬜ |
| 7 | Contract selection | ⬜ |
| 8 | Premium trade engineering (entry/SL/TP) | ⬜ |
| 9 | Risk validation | ⬜ |
| 10 | Signal output + runtime (full state machine) | 🚧 (state machine + runtime shell exist; only STARTUP..DATA_VALIDATION are live) |
| 11 | Replay/testing infrastructure using the live decision functions | ⬜ |

## Why the stop point is here

Phases 3–9 are the actual trading logic — structure detection, the
authorization gate, contract selection, and premium SL/TP engineering.
Per section 29 of the brief ("optimize for correctness of the decision
process," not for producing a signal), these deserve their own dedicated,
reviewed passes rather than being generated in bulk in the same sitting as
the plumbing. Phases 1–2 (this commit) give the rest of the system a
data foundation — a client that won't crash on a missing optional field, a
`SourcedField` type that makes "how fresh/available is this?" a first-class
question everywhere — that Phases 3+ build directly on top of.

Also material: this sandbox cannot reach the real upstream host (see
`docs/ENDPOINTS.md`). Building the structure/authorization/option-selection
logic against unverified field names risks building the wrong thing twice.
`scripts/probe_upstream.py` (Phase 2) exists specifically so that, once run
somewhere with network access to `140.245.226.102:10000`, its captured
sample payloads can correct `data/models.py` before Phase 3 commits to a
canonical snapshot shape.

## Next steps (Phase 3+)

1. Run `scripts/probe_upstream.py` against production and commit the
   captured samples under `tests/fixtures/upstream_samples/`.
2. Reconcile `data/models.py` against the real payloads; adjust
   `docs/ENDPOINTS.md`.
3. Build `domain/snapshot.py` (`MarketSnapshot`) and `market/aggregation.py`
   against the corrected models, with tests for candle-boundary/incomplete-
   bar handling and duplicate-snapshot detection.
4. Continue phase-by-phase per the table above, committing and reporting
   after each.
