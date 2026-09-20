# Signal schema

Implemented as pydantic models in `psygrid_option_engine/signals/schema.py`
(`schema_version = "1.0"`). Two possible terminal payloads, discriminated by
`state`.

## Common envelope (`SignalBase`)

```
schema_version: str            # "1.0"
engine: str                    # "PSYGRID_OPTION_ENGINE"
signal_id: str                 # uuid4, unique per decision cycle - the one
                                # field that legitimately differs between
                                # two decide() calls on identical input
                                # (see the live/replay parity test)
decision_timestamp: datetime   # UTC, == the snapshot's as_of
underlying: Literal["NIFTY", "BANKNIFTY"]
state: Literal["TRADE_READY", "NO_TRADE"]
data_quality: DataQuality      # freshness/availability summary, see below
tier: int                      # 0-4; see authorization/tiers.py. 0 on every
                                # early-exit NO_TRADE (critical data gate,
                                # missing LTP); >=1 required for TRADE_READY
market_state: dict             # see "market_state contents" below
structure: dict                # StructureEngine output (regime, trend bias,
                                # invalidation levels, liquidity zones)
authorization: dict            # framework/tier rationale
option_analysis: dict          # candidate + selection rationale
risk: dict                     # RiskValidation output
reasons: list[str]              # REQUIRED, non-empty on both variants
```

## `NO_TRADE` (`NoTradeSignal`)

Adds:

```
state: Literal["NO_TRADE"]
best_developing_setup: DevelopingSetup | None
  framework: str
  direction: Literal["CALL", "PUT"]
  tier: int
  tier_label: str
  evidence_summary: list[str]
  missing_confirmation: list[str]
  upgrade_condition: str
  invalidation_condition: str
```

`best_developing_setup` is populated whenever at least one strategy
framework was applicable to the observed regime, even if it never reached
an actionable tier (brief section 22: "no trade, but here is the current
state" rather than silence). It is `None` only when *no* framework was
applicable at all (e.g. an `UNCERTAIN` regime), or when the signal was
short-circuited before authorization ran (critical data unavailable,
missing LTP).

`reasons` is required and non-empty for every `NO_TRADE` signal — an empty
reasons list fails schema validation. This is deliberate: the brief
requires explicit reasoning for every `NO_TRADE`, not a bare status code.

## `TRADE_READY` (`TradeReadySignal`)

Adds:

```
state: Literal["TRADE_READY"]
direction: Literal["CALL", "PUT"]
contract: ContractRef
  security_id: str
  symbol: str
  underlying: Literal["NIFTY", "BANKNIFTY"]
  expiry: date
  strike: float
  option_type: Literal["CE", "PE"]
execution: ExecutionPlan
  entry: float
  stop_loss: float
  take_profit: float
  risk_reward: float             # (take_profit - entry) / (entry - stop_loss)
  structural_invalidation: str   # human-readable underlying-level description
  underlying_invalidation_level: float
execution_quality: ExecutionQuality
  spread_pct: float
  depth_assessment: str
  liquidity_assessment: str
```

(`market_state`/`structure`/`authorization`/`option_analysis`/`risk`/
`reasons` are the common-envelope fields above, populated the same way
for both variants.)

## `market_state` contents

Not a separately-versioned type (it's the common envelope's loosely-typed
`dict`), but these keys are always present once `decide()` reaches
structure analysis (i.e. on every signal except an early critical-data/
missing-LTP exit, where `market_state` is `{}`):

```
ltp: float
price_change_pct: float | None    # vs. the canonical session open (M1-
                                    # derived when the raw payload has no
                                    # top-level day-open field, which is
                                    # the normal case in production)
vwap: float | None
vwap_relation: str | None          # "ABOVE" / "BELOW" / "AT"
session_high, session_low: float | None
expected_range_upper, expected_range_lower: float | None   # None (with
    # range_source=None) when neither ATR nor VIX is available yet -
    # never a calibrated probability (brief section 33)
expected_daily_range, expected_remaining_range: float | None
range_source: str | None           # "ATR+VIX blend" / "ATR only (VIX
                                    # unavailable)" / "VIX-implied only
                                    # (daily ATR unavailable)" / None
atr_daily_component, vix_implied_daily_move_component: float | None
session_range_so_far, range_utilization_pct, time_remaining_minutes: float | None
current_week_high, current_week_low: float | None   # only from genuine
    # multi-day D1 history - never derived from a single session's candles
week_range_status: Literal["AVAILABLE", "INSUFFICIENT_HISTORY"]
data_quality: str                  # == data_quality.overall
```

## `DataQuality`

```
overall: Literal["GOOD", "DEGRADED", "INSUFFICIENT"]
critical_endpoints_ok: bool
stale_fields: list[str]
unavailable_fields: list[str]
per_source: dict[str, SourceStatus]
  SourceStatus:
    fetched_at: datetime | None
    observed_at: datetime | None
    age_seconds: float | None
    available: bool
    status: Literal["OK", "STALE", "MISSING", "ERROR"]
```

`critical_endpoints_ok` is `True` only when every critical endpoint
(`underlying`, `options`, `depth`) is genuinely usable, not merely
fetched: `status == "OK"` for each (which itself requires no fetch error,
no structural-validation issue, no future-dated timestamp, and freshness
within tolerance — a `STALE`/`ERROR` critical source makes this `False`,
not just `DEGRADED`), *and* the canonical data actually extractable from
it (e.g. a real underlying LTP, at least one parsed option leg) — see
`data/validation.py::build_data_quality`'s `critical_extraction_ok`
parameter. `False` here always forces `NO_TRADE` (`api/decision.py`'s
first check) regardless of anything else in the pipeline. A payload PSYGRID
itself marks as synthetic/placeholder (`synthetic_data`/
`synthetic_candles: true`) is treated as `ERROR`, the same as a
structurally invalid one.

## Validation rules enforced by the schema itself (not downstream code)

- `reasons` non-empty on both variants.
- `risk_reward > 0` on `TRADE_READY` (a non-positive R:R can never reach
  this schema; `risk/` must reject it first, but the schema also refuses
  to serialize an invalid one — defense in depth).
- `strike > 0`, `entry > 0`, `stop_loss != entry`, `take_profit != entry`.
- For `CALL`: `take_profit > entry > stop_loss`. For `PUT`:
  `take_profit_premium > entry > stop_loss` still holds because SL/TP are
  always expressed in **premium space** (the option's own price), not
  underlying-direction space — a premium stop is always below the premium
  entry and a premium target always above it, regardless of CALL vs PUT,
  because you are long the option in both cases (this engine never sells/
  writes options — see `docs/SAFETY.md` and section 26 of the brief, which
  scopes this to option *trades*, not synthetic underlying direction via
  premium math).
- `schema_version` is pinned; a mismatched version fails fast rather than
  silently accepting a differently-shaped payload from e.g. `replay/`.
