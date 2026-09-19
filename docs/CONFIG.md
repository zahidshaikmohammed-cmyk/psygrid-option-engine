# Configuration reference

All settings are in `psygrid_option_engine/config/settings.py`
(`Settings`, a `pydantic-settings` model), sourced from environment
variables prefixed `PSYGRID_`, or a `.env` file (git-ignored). See
`.env.example` for the full list with defaults.

| Variable | Default | Meaning |
|---|---|---|
| `PSYGRID_BASE_URL` | `http://140.245.226.102:10000` | Upstream PSYGRID base URL. |
| `PSYGRID_HTTP_TIMEOUT_SECONDS` | `5.0` | Total per-request timeout. |
| `PSYGRID_HTTP_CONNECT_TIMEOUT_SECONDS` | `3.0` | Connect-phase timeout. |
| `PSYGRID_MAX_RETRIES` | `3` | Retries on transient failure (timeout/5xx/connection error), exponential backoff. |
| `PSYGRID_RETRY_BACKOFF_SECONDS` | `0.5` | Base backoff; actual wait is `backoff * 2**attempt` with jitter, capped. |
| `PSYGRID_FRESHNESS_TOLERANCE_*_SECONDS` | see `.env.example` | Per-endpoint-class max age before a field is `STALE` rather than `OK`. Deliberately per-class, not global — see `config/settings.py`. |
| `PSYGRID_DECISION_INTERVAL_SECONDS` | `15.0` | How often the runtime loop re-enters `DATA_LOADING`. |
| `PSYGRID_MARKET_OPEN` / `PSYGRID_MARKET_CLOSE` | `09:15` / `15:30` | Session window, IST wall-clock (`HH:MM`). |
| `PSYGRID_OPENING_PERIOD_MINUTES` | `15` | Length of the `OPENING` session phase after open. |
| `PSYGRID_ENTRY_CUTOFF` | `15:00` | No new trade entries at/after this IST time. |
| `PSYGRID_LATE_SESSION_START` | `14:30` | Start of `LATE_SESSION` phase. |

`Settings.session_window()` builds a `config.session.SessionWindow` from
the relevant fields — engine code should call this rather than
constructing `SessionWindow` with hard-coded defaults, so overrides
actually take effect.

`get_settings()` returns a process-wide cached instance
(`functools.lru_cache`). Tests must construct `Settings(...)` directly
(bypassing the cache) so each test gets an isolated, explicit
configuration — never mutate the cached singleton in a test.
