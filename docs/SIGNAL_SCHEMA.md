# Signal schema

Implemented as pydantic models in `psygrid_option_engine/signals/schema.py`
(`schema_version = "1.0"`). Two possible terminal payloads, discriminated by
`state`.

## Common envelope (`SignalBase`)

```
schema_version: str            # "1.0"
engine: str                    # "PSYGRID_OPTION_ENGINE"
signal_id: str                 # uuid4, unique per decision cycle
decision_timestamp: datetime   # UTC, when this decision was made
underlying: Literal["NIFTY", "BANKNIFTY"]
state: Literal["TRADE_READY", "NO_TRADE"]
data_quality: DataQuality      # freshness/availability summary, see below
reasons: list[str]             # always populated for NO_TRADE; may carry
                                # informational notes for TRADE_READY
```

## `NO_TRADE` (`NoTradeSignal`)

Adds:

```
state: Literal["NO_TRADE"]
last_authorization: AuthorizationState | None   # best info available even
                                                  # when short-circuited early
structure: dict | None           # present if structure analysis ran
authorization: dict | None       # present if authorization ran
option_analysis: dict | None     # present if contract selection ran
risk: dict | None                # present if risk validation ran
reasons: list[str]               # REQUIRED, non-empty
```

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
market_state: dict               # summarized MarketSnapshot evidence
structure: dict                  # StructureEngine output
authorization: dict              # AuthorizationEngine output
option_analysis: dict            # candidate + selection rationale, incl.
                                  # rejected contracts and why
risk: dict                       # RiskValidation output (all checks, pass)
execution_quality: ExecutionQuality
  spread_pct: float
  depth_assessment: str
  liquidity_assessment: str
reasons: list[str]               # supporting evidence, non-empty
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
