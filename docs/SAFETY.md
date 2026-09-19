# Safety boundaries

## No order execution

This repository is a **signal/decision engine only**. There is, and must
never be:

- a Dhan (or any broker) order-placement API integration,
- any code path that submits a live order,
- any code path that submits a paper/simulated order to a broker API,
- any "auto-execute" mode.

`risk/` and `signals/` produce a `Signal` object / JSON payload. Nothing in
this codebase consumes that payload to place an order. If a future
requirement adds paper-trading simulation, it must be a clearly separate,
explicitly-requested addition — not implied by this build.

## No LLM in the deterministic core

No module under `psygrid_option_engine/{data,domain,market,structure,
authorization,options,execution,risk,signals,replay}/` may import an LLM
client or call an LLM API. All decision math is deterministic Python,
runnable and reproducible offline.

If an LLM-based explanation/interpretation layer is added later (out of
scope for this build), it must:

- only be given already-built, read-only `Signal`/`MarketSnapshot` objects,
- never fetch its own market data,
- never be able to change `state`, `direction`, `contract`, or `execution`
  fields on a signal it's explaining,
- be structurally incapable of "overriding" a `NO_TRADE` into a trade.

## Secrets

- No credentials are hard-coded anywhere in this repository.
- `config/settings.py` reads all secrets (if any upstream auth is ever
  required — currently the documented endpoints appear to be public/
  unauthenticated) from environment variables via `pydantic-settings`,
  never from checked-in files. `.env` is git-ignored; `.env.example`
  documents the variable names with placeholder values only.
- Logging (`config/logging.py` once added) must never emit full request
  headers or any field named/matching `*key*`, `*secret*`, `*token*`,
  `*credential*`, `*password*` at any log level. This is enforced by a
  redaction filter, not by convention alone — see the logging test in
  Phase 2's test suite.

## Upstream boundary

This engine only performs read-only `GET` requests against the documented
PSYGRID public JSON endpoints. It never writes to, authenticates
destructively against, or otherwise mutates the upstream service. It does
not scrape any other site (see section 20 of the brief — no NSE/Yahoo/
Google scraping).
