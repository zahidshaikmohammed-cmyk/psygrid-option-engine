# PSYGRID Option Engine

Intraday intelligence and option-trading **decision** engine for NIFTY and
BANKNIFTY. Consumes the PSYGRID live-data API (a separate, external, unmodified
upstream service) and produces a strictly-typed `TRADE_READY` or `NO_TRADE`
signal — never an order. See `docs/SAFETY.md`: this engine never places
broker orders.

**Status: Phases 0–10 of 11 implemented, plus a production-hardening pass**
(replay's capture/replay machinery exists; outcome measurement and
calibration don't yet — see `docs/PHASES.md`). The full pipeline — data
ingestion, structure/momentum/volatility/futures/chain/breadth analysis,
the authorization/confluence/tier engine, contract selection, premium
execution engineering, and risk validation — is real, tested,
deterministic logic, wired end to end through `run_engine.py`. The
production-hardening pass (`docs/PHASES.md`, "Production hardening")
closed several concrete correctness/safety gaps found by auditing against
the codebase's own "no fabrication, fail closed" design rule: a critical
endpoint that's stale/structurally-invalid/future-dated/synthetic-flagged
now genuinely blocks `TRADE_READY` (not just an HTTP-level check), a
fabricated depth quote and a fabricated "weekly range" were removed, two
fully-built evidence modules that were never actually wired in now are,
and `--live` mode now force-exits an active trade at the 15:00 IST cutoff
and cannot immediately resurrect a just-resolved setup.

**✅ The upstream field-name contract was verified against a real
production payload on 2026-09-19** for most endpoints (see
`docs/ENDPOINTS.md`) — `data/snapshot_builder.py` was corrected against
`artifacts/production_endpoint_samples.json`, with
`tests/test_production_contract.py` replaying the real bytes as a
permanent regression check. This closed concrete bugs where the adapter
was silently parsing the real options chain and depth ladders to zero
entries. `futures`, `indicators`, and `rbi_news` returned HTTP 503 at
capture time (no real sample yet) and remain unverified; do not treat a
live run's `futures`/`indicators`/`rbi_news`-derived evidence as
confirmed until a fresh probe captures them with the market open.
`docs/PHASES.md` has the full honest rundown of what's solid v1 vs. what's
a known, documented gap.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layering, information
  boundary, why the layers are split the way they are.
- [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md) — upstream data contract
  (✅ verified against a real payload for most endpoints — see that doc).
- [`docs/STATE_MACHINE.md`](docs/STATE_MACHINE.md) — engine states and
  transitions.
- [`docs/SIGNAL_SCHEMA.md`](docs/SIGNAL_SCHEMA.md) — `TRADE_READY` /
  `NO_TRADE` output schema.
- [`docs/PHASES.md`](docs/PHASES.md) — phase-by-phase plan and status.
- [`docs/SAFETY.md`](docs/SAFETY.md) — no order execution, no LLM in the
  core, secrets handling.
- [`docs/CONFIG.md`](docs/CONFIG.md) — configuration reference.

## Requirements

Python 3.11+.

## Install (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

See [`docs/CONFIG.md`](docs/CONFIG.md) for every variable.

## Run

```bash
python run_engine.py --once [--underlying NIFTY|BANKNIFTY|BOTH]
python run_engine.py --live [--interval SECONDS]
```

Runs the full intelligence cycle and prints a human-readable report plus
the current best opportunity (or an honest `NO_TRADE` with the strongest
developing setup, what's missing, and what would upgrade/invalidate it).
`--live` repeats this on an interval, printing only when a tracked setup's
state actually changes. **Signal-only — see `docs/SAFETY.md`: this process
never places a broker order.**

There is also a lower-level entrypoint that stops after data loading/
validation, useful for checking upstream connectivity/data-quality alone:

```bash
python -m psygrid_option_engine --underlying NIFTY
```

## Test

```bash
pytest
```

## Probing the real upstream contract

This repository was built in a sandbox with no network route to the
production PSYGRID host. `scripts/probe_upstream.py` fetches every
registered endpoint and writes the raw responses to a local directory, for
use in a network-enabled environment to verify/correct `docs/ENDPOINTS.md`
and `psygrid_option_engine/data/models.py`:

```bash
python scripts/probe_upstream.py --base-url http://140.245.226.102:10000 --out ./upstream_samples
```

## License

Proprietary — internal project.
