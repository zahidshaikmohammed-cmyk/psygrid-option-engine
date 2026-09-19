# PSYGRID Option Engine

Intraday intelligence and option-trading **decision** engine for NIFTY and
BANKNIFTY. Consumes the PSYGRID live-data API (a separate, external, unmodified
upstream service) and produces a strictly-typed `TRADE_READY` or `NO_TRADE`
signal — never an order. See `docs/SAFETY.md`: this engine never places
broker orders.

**Status: Phases 1–2 of 11 implemented.** The upstream client, config, core
schemas, and data validation are real and tested. The trading logic itself
(structure, authorization, option selection, execution, risk) is not yet
implemented — see `docs/PHASES.md` for the plan and honest status per
phase. Do not treat anything this repository currently outputs as a trade
recommendation.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layering, information
  boundary, why the layers are split the way they are.
- [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md) — upstream data contract
  (⚠️ unverified against live upstream — see that doc).
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
python -m psygrid_option_engine --underlying NIFTY
```

Today this runs the engine through `DATA_LOADING` → `DATA_VALIDATION` and
reports what it fetched, whether critical data is present and fresh, and
exits — it does not yet produce a trading signal (Phases 3–9 are not
implemented). See `docs/PHASES.md`.

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
