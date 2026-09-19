# PSYGRID upstream data contract

## ⚠️ Verification status

**This contract has NOT been verified against a live upstream response.**
The sandbox this repository was built in has no route to
`http://140.245.226.102:10000` (outbound network here is HTTPS-only through
an egress proxy; plain HTTP to that IP times out). Everything below is
transcribed from the endpoint list and field descriptions given in the task
brief, not from an inspected payload.

Consequences of this, by design:

1. `data/models.py` response models use `extra="allow"` and treat almost
   every field as `Optional` — the client must not crash or silently
   coerce data if a real field is named, typed, or nested differently than
   assumed here.
2. `data/validation.py` distinguishes **structural** validation (is this
   JSON, does it have the fields we treat as load-bearing) from
   **semantic** assumptions (this repo does not hard-code e.g. a specific
   strike-step or lot size — those must come from data or documented
   config, per section 3 of the brief).
3. Before Phase 3 (canonical `MarketSnapshot`) is built against real field
   names, **someone with network access to the upstream host should run
   `scripts/probe_upstream.py` (added in this phase) and commit the
   captured sample payloads** so the models can be corrected against
   ground truth rather than assumption. Until then, treat every field name
   below as "best guess, pending confirmation."

## Endpoint inventory (as given in the brief)

| Endpoint | Purpose (assumed) | Update cadence (assumed) |
|---|---|---|
| `/public/nifty.json` | NIFTY underlying index snapshot (LTP/OHLC) | intraday, frequent |
| `/public/banknifty.json` | BANKNIFTY underlying index snapshot | intraday, frequent |
| `/public/nifty-options.json` | NIFTY option chain | intraday, frequent |
| `/public/banknifty-options.json` | BANKNIFTY option chain | intraday, frequent |
| `/public/nifty-depth.json` | NIFTY market depth (underlying/futures/options) | intraday, frequent |
| `/public/banknifty-depth.json` | BANKNIFTY market depth | intraday, frequent |
| `/public/nifty-indicators.json` | Pre-computed indicators for NIFTY | intraday |
| `/public/banknifty-indicators.json` | Pre-computed indicators for BANKNIFTY | intraday |
| `/public/nifty-futures.json` | NIFTY futures (nearest/other expiries) | intraday |
| `/public/banknifty-futures.json` | BANKNIFTY futures | intraday |
| `/public/market-breadth.json` | Advance/decline and breadth stats | intraday |
| `/public/sectors.json` | Sector-level performance | intraday |
| `/public/global-context.json` | Delayed macro series (S&P 500, VIX, US10Y, WTI, USDINR) | delayed / periodic |
| `/public/rbi-news.json` | RBI-sourced news items | periodic |
| `/public/live.json`, `/public/live-*.json` | General/other live feeds (unspecified shape) | intraday |
| `/public/stock/{symbol}.json` | Per-stock feed (used for sector/breadth drill-down, not core to NIFTY/BANKNIFTY options) | intraday |
| `/public/indiavix.json` | India VIX | intraday |

`data/endpoints.py` is the single place these paths are registered — no
other module may hard-code a path string.

## Field-level contract

### Underlying (`nifty.json`, `banknifty.json`)

Assumed shape (loose): `{ "symbol", "ltp", "open", "high", "low", "close",
"volume"?, "timestamp"/"as_of", "ohlc_1m"?/"candles_1m"? [...] }`. The brief
lists 1m/5m/15m/1H/daily/weekly OHLC as desired but does not confirm which
of these the raw underlying endpoint carries directly vs. which this engine
must aggregate itself (see `market/aggregation.py`, Phase 3+). Until
confirmed, the client treats only whatever timeframe arrays are actually
present as source data, and everything else as aggregated-and-labeled-as-
such.

### Options (`nifty-options.json`, `banknifty-options.json`)

Assumed shape: a list of strikes, each with CE/PE legs carrying
`security_id, symbol, expiry, strike, ltp/premium, bid, ask, volume, oi,
oi_change, iv, delta, gamma, theta, vega` where available. Per section 10
of the brief, Greeks/IV may be **absent** — `data/models.py` marks these
`Optional[float] = None` and `domain/field.py` wrapping downstream marks
them `available=False` rather than defaulting to `0.0` (a `0.0` delta is a
real, meaningful value and must never be confused with "missing").

### Depth (`*-depth.json`)

Assumed shape: best bid/ask ladders (N levels) per instrument, keyed by
`security_id`. Used for spread/liquidity checks in `risk/`, not decision
logic on its own.

### Indicators (`*-indicators.json`)

Treated as **evidence, not ground truth**: the brief explicitly forbids a
single-indicator strategy. If present, these values are compared against
this engine's own deterministic computation (`market/indicators.py`) where
inputs overlap, and a mismatch beyond tolerance is a data-quality flag, not
silently trusted.

### Futures (`*-futures.json`)

Assumed shape: `{ expiry, ltp, oi, oi_change, volume, bid, ask, ohlc }` per
expiry. Used for basis and participation evidence in `structure/` and
`authorization/` (Phase 4/5), not for option selection directly.

### Breadth / sectors (`market-breadth.json`, `sectors.json`)

Assumed shape: advance/decline counts and sector index moves. Contextual
evidence only in `authorization/`.

### `indiavix.json`

Assumed shape: `{ value, timestamp }` or similar. Used as a volatility
context input, never as the sole volatility measure (ATR/realized vol from
the underlying itself is computed independently).

### `global-context.json`

Per section 19 of the brief: delayed macro series (S&P 500, VIX, US10Y,
WTI, USDINR) — **each series keeps its own `source_date`** and is never
treated as live-tick data. Fields not present in this feed (DXY, NASDAQ,
Dow, GIFT NIFTY, Gold, Asian indices) are represented as
`SourcedField(available=False)`, never substituted with a different
dataset.

### `rbi-news.json`

Assumed shape: a list of `{ headline, published_at, ... }`. Used only as
contextual event-risk evidence (e.g. "known scheduled RBI item today")
never as a sentiment score.

### `live.json` / `live-*.json` / `stock/{symbol}.json`

Shape unspecified in the brief. `data/endpoints.py` registers the ones
named (`live.json`) as **optional/best-effort** — the client fetches them
if configured but their absence is never treated as a critical failure
(per section 5 of the brief: "must never crash simply because one optional
endpoint is unavailable").

## Critical vs. optional endpoints

Per section 5 of the brief ("critical missing data must prevent unsafe
signal generation"), `config/settings.py` classifies endpoints as
`CRITICAL` or `OPTIONAL` per underlying:

- **CRITICAL** (missing/stale → the pipeline cannot reach `TRADE_READY`
  for that underlying): underlying snapshot, options chain, depth for
  options.
- **OPTIONAL** (missing/stale → downgrades data-quality/confidence,
  recorded in `data_quality`, but does not by itself force `NO_TRADE`):
  indicators, futures, breadth, sectors, global-context, rbi-news, vix
  (VIX is borderline — see `config/settings.py::CRITICAL_ENDPOINTS` for the
  authoritative, documented list; it currently treats VIX as optional
  evidence since it is contextual, not a trade input by itself).

This list is deliberately centralized in one config object so a future
verification pass against the real upstream can correct it without hunting
through the codebase.
