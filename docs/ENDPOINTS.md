# PSYGRID upstream data contract

## ✅ Verification status (updated 2026-09-19)

**This contract has been partially verified against a real upstream
response.** `artifacts/production_endpoint_samples.json` was captured from
the Oracle host via `scripts/probe_upstream.py` on 2026-09-19 (market
CLOSED at capture time). `psygrid_option_engine/data/snapshot_builder.py`
has been corrected against it and `tests/test_production_contract.py`
replays the real captured bytes through the adapter as a permanent
regression check.

**Verified** (real sample obtained, adapter corrected and tested against
it): `underlying`, `options`, `depth`, `market_breadth`, `sectors`
(container shape only — the sample's `sectors` list itself was empty, so
per-item field names remain unconfirmed), `india_vix`, `global_context`.

**Still unverified** (endpoint returned HTTP 503 at capture time — no real
sample exists yet): `futures`, `indicators`, `rbi_news`. These three keep
their original best-guess field names below; treat any live run depending
on them as unverified until a fresh probe captures them with the market
open (503 strongly suggests these generators only run intraday).

**Also unverified even for the "verified" endpoints above**: anything only
observable with the market open — a populated (non-null) LTP, non-empty
intraday candle bars, non-zero depth levels, a populated `sectors` array
entry. The 2026-09-19 sample was captured on a closed market, so those
specific values were `null`/empty/zero in the real payload and the adapter
correctly reports them as unavailable rather than guessing — but the
*field names themselves* (verified from the schema/keys present, and from
non-zero option-chain OI/greeks/quotes which the payload did carry even
closed) are confirmed.

See `psygrid_option_engine/data/snapshot_builder.py`'s module docstring
and each `_build_*` function's docstring for the specific real shape vs.
what was originally guessed, and `docs/PHASES.md` for the full list of
concrete bugs this fixed (options chain parsing to zero legs, depth
parsing to zero entries, global_context reading metadata keys instead of
the real nested series, market_breadth's advancing/declining field names).

The remaining unverified endpoints still follow the same design
discipline that made this correction a localized fix rather than a
rewrite:

1. `data/models.py` response models use `extra="allow"` and treat almost
   every field as `Optional` — the client must not crash or silently
   coerce data if a real field is named, typed, or nested differently than
   assumed here.
2. `data/validation.py` distinguishes **structural** validation (is this
   JSON, does it have the fields we treat as load-bearing) from
   **semantic** assumptions (this repo does not hard-code e.g. a specific
   strike-step or lot size — those must come from data or documented
   config, per section 3 of the brief).
3. For `futures`/`indicators`/`rbi_news`, treat every field name below as
   "best guess, pending confirmation" until a fresh probe (market open)
   captures a real sample for them. See "Oracle probe procedure" below for
   the exact steps to re-run.

## Oracle probe procedure

Run this from the Oracle host that actually runs the PSYGRID upstream
service (SSH access, not this sandbox). It performs read-only `GET`
requests only — it never writes to, deploys to, or modifies the upstream
service in any way.

### Step 0 (optional, read-only) — look for endpoints not already known

`scripts/probe_upstream.py` automatically probes every endpoint currently
registered in `psygrid_option_engine/data/endpoints.py`, which covers all
15 endpoints listed in the brief plus `/public/live.json`. It does **not**
know the `/public/live-*.json` variant names or which `/public/stock/
{symbol}.json` symbols exist — those need to be discovered once, from the
box itself, without guessing:

```bash
# Find what's actually listening on :10000 (read-only)
sudo ss -ltnp | grep :10000        # or: sudo lsof -i :10000

# If it's an application process, find its working directory (read-only)
ps -fp <PID_FROM_ABOVE>
readlink -f /proc/<PID_FROM_ABOVE>/cwd

# If that directory (or wherever it serves static files from) is
# filesystem-readable, list it — this reveals every real endpoint
# filename directly, more reliably than guessing:
ls -la <served_dir>/public/

# If it's an application with route definitions, grep them (read-only):
grep -rniE "public/|@app\.(get|route)|router\.(get)" <app_dir> --include=*.py
grep -rniE "public/|app\.get\(|router\.get\(" <app_dir> --include=*.js

# Also worth a quick, safe curl in case a manifest/listing exists:
curl -s http://127.0.0.1:10000/public/ | head -c 500
curl -s http://127.0.0.1:10000/public/index.json | head -c 500
```

Whatever extra filenames this turns up (e.g. `live-nifty.json`, or a
symbol list to build `stock/{symbol}.json` paths from), pass them to the
probe via repeated `--extra /public/<name>.json` flags in Step 2.

### Step 1 — get this repo and its base dependencies onto the box

```bash
git clone https://github.com/zahidshaikmohammed-cmyk/psygrid-option-engine.git
cd psygrid-option-engine
git fetch origin claude/friendly-hamilton-ghgt6v
git checkout claude/friendly-hamilton-ghgt6v
git pull origin claude/friendly-hamilton-ghgt6v

python3 -m venv .venv
source .venv/bin/activate
pip install -e .   # base deps only (httpx/pydantic/tenacity); no dev/test extras needed
```

(If the repo is already checked out on the box, skip `git clone` and just
`git pull` inside the existing checkout.)

### Step 2 — run the probe

```bash
python scripts/probe_upstream.py \
  --base-url http://127.0.0.1:10000 \
  --out artifacts/production_endpoint_samples.json \
  --timeout 10 \
  --retries 1
  # add --extra /public/whatever.json (repeatable) for anything found in Step 0
```

`--base-url http://127.0.0.1:10000` is preferred over the public IP since
the script is running on the same host — it avoids any external
routing/firewall variables. Swap in `http://140.245.226.102:10000` if
`127.0.0.1` doesn't reach the service for some local reason.

Every line printed should say `OK`; a `FAIL` line names a path that
returned an error, a non-2xx status, or invalid JSON — expected for
endpoints that don't actually exist (e.g. speculative `--extra` guesses),
worth a second look for endpoints from the brief's list of 15.

### Step 3 — verify redaction before doing anything else with the file

The script redacts by key name (`*key*`, `*secret*`, `*token*`,
`*password*`, `*credential*`, `*auth*`, `dhan*`, `fred*api*`) and by value
shape (long opaque token-like strings, `Bearer `/`sk-`/`xox` prefixes),
but verify it yourself before this file leaves the box:

```bash
grep -inE '"(api[_-]?key|secret|token|password|passwd|credential)"' \
  artifacts/production_endpoint_samples.json | grep -v REDACTED
# This must print NOTHING. If it prints anything, STOP — do not transfer
# the file — and tell me which field it flagged so the redaction rules
# (or the endpoint contract itself) can be fixed first.
```

### Step 4 — get the artifact to me (pick one)

```bash
# (a) scp to your local machine, then hand me the file directly
scp <oracle-user>@<oracle-host>:~/psygrid-option-engine/artifacts/production_endpoint_samples.json .

# (b) OR, if this checkout can push to the branch, commit and push it here
#     and tell me you did so:
git add artifacts/production_endpoint_samples.json
git commit -m "Add production endpoint samples captured via probe_upstream.py"
git push origin claude/friendly-hamilton-ghgt6v
```

Once I have the artifact, I'll reconcile `docs/ENDPOINTS.md` and
`psygrid_option_engine/data/models.py` against the real payloads before
starting Phase 3.

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

### Underlying (`nifty.json`, `banknifty.json`) — ✅ verified 2026-09-19

Real shape: `{ "symbol", "ltp", "ltp_timestamp", "security_id",
"instrument", "exchange_segment", "schema_version", "session": {
"current_time_ist", "date", "status", "timezone" }, "candle_source",
"synthetic_candles", "feed": {...}, "timeframes": ["1m","5m","15m","1h"],
"1m": [...], "5m": [...], "15m": [...], "1h": [...] }`. Candle arrays are
**top-level keys named after the timeframe** (`"1m"`, not nested under
`timeframes`, which is just a manifest listing which top-level keys
exist). **There is no daily/weekly candle array and no `prev_day`/
`prev_week` OHLC field at all** — confirmed absent, not merely unpopulated
— so `current_week_high/low`, a genuine daily ATR, and day-over-day
comparisons must come from accumulated history (`replay/` or a future live
accumulation loop), never a raw field. There is also **no top-level
open/high/low/close for "today"** — `structure/levels.py` already derives
today's high/low/open from closed M1 candles when `day_ohlc` is
unavailable, which is exactly this case.

### Options (`nifty-options.json`, `banknifty-options.json`) — ✅ verified 2026-09-19

Real shape: `{ "expiry": "<ISO date>", "expiry_list": [...],
"underlying_ltp", "analytics": {...}, "strikes": [ { "strike": <float>,
"ce": {...}, "pe": {...} }, ... ] }`. Each `ce`/`pe` leg carries
`security_id, last_price, top_bid_price, top_bid_quantity, top_ask_price,
top_ask_quantity, oi, previous_oi, volume, previous_volume,
average_price, previous_close_price, implied_volatility, greeks: {delta,
gamma, theta, vega}`. Notably:
- **`expiry` is chain-level, not per-leg** — the whole payload is a single
  expiry's chain; there is no per-leg expiry field.
- **Greeks are nested one level deeper** under `ce.greeks`/`pe.greeks`,
  not flat on the leg.
- **No direct `oi_change` field** — the adapter computes it as
  `oi - previous_oi` (real arithmetic on real observed values, not a
  fabrication).
- No `symbol` field on the leg itself.
- IV/Greeks were present (non-null, non-zero) even with the market closed
  in the captured sample, so their absence when populated would be a
  genuine data-quality signal, not an artifact of market hours.

### Depth (`*-depth.json`) — ✅ verified 2026-09-19

Real shape: `{ "underlying_ltp", "underlying_security_id", "depth_levels":
20, "contracts": [ { "security_id", "strike", "option_type", "expiry",
"last_price", "oi", "volume", "average_price", "ohlc": {open,high,low,
close}, "buy_quantity", "sell_quantity", "crossed_book",
"bid": [ {level, price, quantity, orders} × 20 ], "ask": [ ... × 20 ] },
... ] }`. The container key is `contracts` (not `data`/`results`/...), and
bid/ask are **singular** (`bid`/`ask`, not `bids`/`asks`). Each depth
level's own `price`/`quantity`/`orders` field names matched the original
guess. The adapter only extracts `bid`/`ask` ladders per `security_id`
into `InstrumentDepth` (per `domain/snapshot.py`) — the contract's own
`oi`/`volume`/`last_price`/`ohlc`/`strike`/`option_type`/`expiry` fields
are not surfaced here since that data already comes from the `options`
endpoint; depth is deliberately kept to bid/ask microstructure only.

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

### Breadth / sectors (`market-breadth.json`, `sectors.json`) — ✅ verified 2026-09-19

`market_breadth` real shape: `{ "as_of", "advancing", "declining",
"unchanged", "advance_decline_ratio", "constituents": [...],
"coverage_count", "new_session_highs", "new_session_lows",
"universe_size" }`. **Real keys are `advancing`/`declining` (full words)**,
not the originally-guessed `advances`/`declines` abbreviations.

`sectors` real shape: `{ "as_of", "sector_count", "sectors": [...],
"universe_size" }` — container key `sectors` confirmed (now in the
adapter's list-container aliases); the captured sample's `sectors` array
was itself empty (market closed), so per-item field names
(`name`/`change_pct` guessed) remain unconfirmed.

### `indiavix.json` — ✅ verified 2026-09-19 (shape only; value unobserved)

Real shape is the **same schema as the underlying endpoint** (`symbol:
"INDIA VIX"`, `ltp`, `ltp_timestamp`, `session`, `timeframes`, per-
timeframe candle arrays), not the originally-guessed `{ value, timestamp }`
shell. The adapter's `ltp`-alias lookup already matches this correctly.
`ltp` was `null` in the captured sample (market closed) so a real non-null
VIX value has not yet been observed — used as a volatility context input,
never as the sole volatility measure (ATR/realized vol from the underlying
itself is computed independently).

### `global-context.json` — ✅ verified 2026-09-19

Real shape: `{ "series": { "sp500": {series_id, source, source_date,
value}, "us_10y_yield": {...}, "usd_inr": {...}, "vix": {...},
"wti_crude_oil": {...} }, "not_available": [...], "market_data_status",
"refresh_seconds", "updated_at" }`. **The real series live nested under a
top-level `series` key** — the original guess treated the whole payload as
a flat `name -> value` map and picked up sibling metadata keys
(`market_data_status`, `refresh_seconds`, ...) as if they were series,
while missing the real ones entirely. `not_available` lists tickers
PSYGRID currently has no source for (in the sample: gift_nifty, nasdaq,
dow_jones, nikkei, hang_seng, shanghai, kospi, dxy, gold) — not consumed
by the adapter, but useful context if a future evidence module wants to
distinguish "this series doesn't exist right now" from "this series
fetch failed." Per section 19 of the brief, **each series keeps its own
`source_date`** and is never treated as live-tick data.

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
