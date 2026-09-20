# Architecture

## 0. Status

This document describes the architecture of `psygrid-option-engine`.
**Phases 0–10 are implemented**: the full pipeline from upstream fetch
through structure/momentum/volatility/futures/option-chain/breadth
evidence, the authorization/confluence/tier engine, contract selection,
premium execution engineering, and risk validation, wired end to end via
`api/decision.py::decide` and runnable through `run_engine.py`. Only
Phase 11 (replay outcome measurement and calibration) remains partial —
the capture/replay machinery exists and is tested, but nothing has
measured real outcomes against it yet. See `docs/PHASES.md` for the
phase-by-phase status and known v1 limitations. The upstream field-name
contract was verified against a real production payload on 2026-09-19
(`artifacts/production_endpoint_samples.json`) and `data/snapshot_builder.py`
was corrected accordingly — `futures`/`indicators`/`rbi_news` remain
unverified (no real sample captured for them yet; see `docs/ENDPOINTS.md`).

## 1. Repository boundary

This repository is a standalone consumer of the PSYGRID live-data API
(`http://140.245.226.102:10000` in production). It never modifies, deploys
to, or depends on internals of that upstream service — it only calls its
public JSON endpoints over HTTP, defensively.

This repository does **not** place broker orders. It is a signal/decision
engine only (see `docs/SAFETY.md`).

## 2. Layering

```
config/          Environment-driven settings, IST session-window rules.
data/            Upstream HTTP client, per-endpoint response models,
                 schema + freshness validation, snapshot capture.
domain/          Canonical internal types: SourcedField[T] (value + source +
                 timestamp + freshness + availability), MarketSnapshot,
                 timeframe enums, aggregation rules.
market/          Deterministic indicator math and timeframe aggregation
                 (VWAP, EMA, ATR, RSI, MACD, ADX, RVOL, Bollinger, Donchian,
                 Supertrend) — pure functions over OHLCV series.
structure/       Market-structure engine: regime, HH/HL/LH/LL, opening
                 range, prior day/week levels, VWAP relation, impulse /
                 retracement, liquidity sweep, failed breakout, structural
                 invalidation levels.
authorization/   Deterministic multi-factor authorization gate:
                 LONG_AUTHORIZED / SHORT_AUTHORIZED / NO_AUTHORIZATION.
                 Never lets one indicator decide alone.
options/         Option-chain parsing, candidate filtering, and constrained
                 contract selection (liquidity, spread, depth, Greeks,
                 moneyness) — never "always ATM" / "highest OI" shortcuts.
execution/       Premium-level entry/stop/target engineering, derived from
                 underlying structural invalidation and option sensitivity,
                 not fixed percentages.
risk/            Pre-trade validation gate. Anything failing here forces
                 NO_TRADE with explicit reasons.
signals/         Versioned, strict output schema (TRADE_READY / NO_TRADE)
                 and the builder that assembles it from the pipeline.
replay/          Deterministic snapshot store + replay driver that reuses
                 the exact same decision functions as the live path — no
                 separate "backtest-only" logic, no look-ahead.
api/             State machine + orchestration entrypoint;
                 `python -m psygrid_option_engine`.
tests/           Unit, property, and integration tests per phase.
```

## 3. The information boundary (non-negotiable)

For a decision made at time `t`, the engine may only use data whose
observation timestamp is `<= t`. This is enforced structurally:

- `data/client.py` stamps every fetched payload with a `fetched_at` wall
  clock time and preserves the upstream-reported timestamp per field.
- `domain/field.py`'s `SourcedField[T]` carries `(value, source, observed_at,
  fetched_at, freshness, available)` for every piece of data that flows
  into a decision — there is no bare `float`/`int` market value anywhere in
  `domain/snapshot.py`.
- `replay/` feeds historical snapshots through the same decision functions
  used live, one snapshot at a time, in chronological order. There is no
  code path that lets a later snapshot's data reach an earlier decision —
  the replay driver only ever holds the snapshot currently "at bat" plus
  read-only history strictly older than it.
- Any higher-timeframe candle built by aggregation (`market/aggregation.py`)
  is only considered "closed" (usable) once the underlying lower-timeframe
  data proves the higher-timeframe bar's period has fully elapsed as of the
  snapshot's boundary time. A forming/incomplete bar is exposed separately
  and tagged `incomplete=True`; decision logic must not treat it as closed.

## 4. Why layers are separated the way they are

- **Structure vs. authorization vs. selection vs. execution** are four
  different questions and four different failure modes:
  1. *What is the market doing?* (structure — observational, no bias)
  2. *Is deploying capital justified right now?* (authorization — a gate,
     produces at most a direction + confidence, never a contract)
  3. *Given a direction, which contract?* (options — a constrained
     selection problem over the chain, not a direction call)
  4. *Given a contract, what premium levels?* (execution — arithmetic over
     structure + option sensitivity, not "target this %")
  Collapsing these into one function is exactly the "EMA9 > EMA20 → CALL"
  failure mode the spec calls out. Keeping them separate means each one is
  independently testable, independently able to say "insufficient
  evidence", and none of them can single-handedly produce a trade.
- **risk/** sits after all of the above as a final gate that can only ever
  downgrade a candidate to `NO_TRADE`, never upgrade one. It never invents
  levels; it only validates ones already computed.
- **signals/** is a pure serialization layer. It must not contain decision
  logic — if it needs to compute something, that logic belongs upstream.

## 5. Data-quality-aware by construction

`SourcedField[T]` (see `domain/field.py`) is the atomic unit almost every
number in this system is wrapped in once it leaves `data/`. This is what
lets `risk/` say "the option depth data is 40s stale, block this trade" or
"IV is unavailable for this contract, exclude it from candidates" without
every downstream layer re-implementing freshness/availability checks.

## 6. LLM boundary

No package under `psygrid_option_engine/` may call an LLM API. All decision
logic is deterministic Python. If a future interpretation/explanation layer
is added, it is additive: it receives the already-built `Signal` /
`MarketSnapshot` objects read-only, may not fetch its own market data, and
cannot alter `state`, `direction`, `contract`, or `execution` fields — see
`docs/SAFETY.md`.

## 7. See also

- `docs/ENDPOINTS.md` — upstream data contract mapping (status: **verified
  against a real production payload for most endpoints as of 2026-09-19**;
  `futures`/`indicators`/`rbi_news` remain unverified — see that
  document's top section).
- `docs/STATE_MACHINE.md` — states and transitions.
- `docs/SIGNAL_SCHEMA.md` — `TRADE_READY` / `NO_TRADE` schema.
- `docs/PHASES.md` — phase plan and current status.
- `docs/SAFETY.md` — safety boundaries (no order execution, no LLM in the
  core).
