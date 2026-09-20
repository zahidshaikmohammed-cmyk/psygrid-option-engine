"""Adapter: `RawFetchBundle` (raw upstream JSON + fetch metadata) ->
canonical `domain.snapshot.MarketSnapshot`.

THIS IS THE ONE MODULE THAT KNOWS ABOUT RAW UPSTREAM FIELD NAMES. As of
2026-09-19, `artifacts/production_endpoint_samples.json` (real payloads
captured from Oracle via `scripts/probe_upstream.py`, market CLOSED at
capture time) verified the field-name contract for: underlying, options,
depth, market_breadth, sectors (container only — item shape was empty in
the sample), india_vix and global_context. The alias tables below were
corrected against that sample, and `options`/`depth`/`global_context`/
`market_breadth` were rewritten to match the real (not guessed) shape —
see each function's docstring for what was verified vs. still inferred.
`futures`, `indicators`, and `rbi_news` returned HTTP 503 at capture time
(no real sample exists for them yet) and remain best-guess/unverified, as
does anything only observable with the market open (a populated LTP,
non-empty candle bars, a non-empty depth level, a populated `sectors`
item). When a fresh sample closes those gaps, this is still the only file
that needs correcting — `domain/snapshot.py` and everything built on it
does not change.

Design rule enforced throughout: a wrong guess about a field name must
degrade to "field unavailable" (a missing `SourcedField`), never raise and
never silently substitute a wrong value. `data_quality` (built the same
way as Phase 2) already tells downstream layers when critical data itself
is missing/stale/invalid; this module adds no new failure mode on top of
that for per-field misses.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.validation import LIST_CONTAINER_KEYS, build_data_quality, extract_timestamp
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import (
    OHLC,
    BreadthSnapshot,
    DepthLevel,
    DepthSnapshot,
    FuturesLeg,
    FuturesSnapshot,
    GlobalContextSeries,
    InstrumentDepth,
    MarketSnapshot,
    NewsItem,
    OptionChainSnapshot,
    OptionLeg,
    OptionType,
    SectorSnapshot,
    UnderlyingSnapshot,
    VixSnapshot,
)
from psygrid_option_engine.domain.timeframe import Candle, Timeframe

# --- alias tables (best guess; see module docstring) ---------------------

_LTP_ALIASES = ("ltp", "last_price", "ltp_price", "price", "last")
_OPEN_ALIASES = ("open", "o", "day_open")
_HIGH_ALIASES = ("high", "h", "day_high")
_LOW_ALIASES = ("low", "l", "day_low")
_CLOSE_ALIASES = ("close", "c", "day_close")
_VOLUME_ALIASES = ("volume", "vol", "total_volume", "total_traded_volume", "qty")
_OI_ALIASES = ("oi", "open_interest")
_OI_CHANGE_ALIASES = ("oi_change", "oichange", "change_in_oi", "oi_chg")
_IV_ALIASES = ("iv", "implied_volatility")
_DELTA_ALIASES = ("delta",)
_GAMMA_ALIASES = ("gamma",)
_THETA_ALIASES = ("theta",)
_VEGA_ALIASES = ("vega",)
_BID_ALIASES = ("top_bid_price", "bid", "bid_price", "best_bid")
_ASK_ALIASES = ("top_ask_price", "ask", "ask_price", "offer", "best_ask")
_STRIKE_ALIASES = ("strike", "strike_price")
_EXPIRY_ALIASES = ("expiry", "expiry_date", "expiryDate")
_SECURITY_ID_ALIASES = ("security_id", "securityid", "id", "token", "instrument_token")
_SYMBOL_ALIASES = ("symbol", "trading_symbol", "tradingsymbol")

_PREV_DAY_ALIASES = ("prev_day", "previous_day", "prevday", "prev_day_ohlc")
_PREV_WEEK_ALIASES = ("prev_week", "previous_week", "prevweek", "prev_week_ohlc")

# Single source of truth shared with data/validation.py's structural
# checks - see that module's LIST_CONTAINER_KEYS docstring for why this
# must never be a second, separately-maintained copy again.
_LIST_CONTAINER_ALIASES = LIST_CONTAINER_KEYS

_CANDLE_TIMEFRAME_ALIASES: dict[Timeframe, tuple[str, ...]] = {
    Timeframe.M1: ("candles_1m", "ohlc_1m", "intraday_1m", "1m", "candles1m"),
    Timeframe.M5: ("candles_5m", "ohlc_5m", "5m", "candles5m"),
    Timeframe.M15: ("candles_15m", "ohlc_15m", "15m", "candles15m"),
    Timeframe.H1: ("candles_1h", "ohlc_1h", "1h", "candles_60m"),
    Timeframe.D1: ("candles_1d", "ohlc_daily", "daily", "candles_d1"),
    Timeframe.W1: ("candles_1w", "ohlc_weekly", "weekly", "candles_w1"),
}


def _lower_map(d: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in d.items()}


def _get_alias(d: Mapping[str, Any] | None, aliases: Sequence[str]) -> Any | None:
    if not d:
        return None
    lowered = _lower_map(d)
    for alias in aliases:
        if alias in lowered and lowered[alias] is not None:
            return lowered[alias]
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    # Python's json module accepts the non-standard NaN/Infinity/-Infinity
    # literals by default; a market-data value can never legitimately be
    # one of these, and letting one through would silently corrupt every
    # downstream comparison/arithmetic (NaN compares False against
    # everything) rather than failing loudly. Treat as unavailable, same
    # as any other unparseable value.
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _as_date(value: Any) -> date | None:
    if isinstance(value, str):
        text = value.strip()
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidate).date()
            except ValueError:
                continue
        try:
            return datetime.strptime(text, "%d-%b-%Y").date()
        except ValueError:
            return None
    return None


def _sf(
    d: Mapping[str, Any] | None,
    aliases: Sequence[str],
    *,
    source: str,
    observed_at: datetime | None,
    fetched_at: datetime | None,
) -> SourcedField[float]:
    raw = _get_alias(d, aliases)
    value = _as_float(raw)
    if value is None:
        return SourcedField.missing(source)
    return SourcedField.of(value, source=source, observed_at=observed_at, fetched_at=fetched_at)


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for alias in _LIST_CONTAINER_ALIASES:
            container = _get_alias(payload, (alias,))
            if isinstance(container, list):
                return [item for item in container if isinstance(item, dict)]
    return []


def _build_ohlc(
    d: Mapping[str, Any] | None,
    *,
    source_prefix: str,
    observed_at: datetime | None,
    fetched_at: datetime | None,
) -> OHLC:
    kw = dict(observed_at=observed_at, fetched_at=fetched_at)
    return OHLC(
        open=_sf(d, _OPEN_ALIASES, source=f"{source_prefix}:open", **kw),
        high=_sf(d, _HIGH_ALIASES, source=f"{source_prefix}:high", **kw),
        low=_sf(d, _LOW_ALIASES, source=f"{source_prefix}:low", **kw),
        close=_sf(d, _CLOSE_ALIASES, source=f"{source_prefix}:close", **kw),
        volume=_sf(d, _VOLUME_ALIASES, source=f"{source_prefix}:volume", **kw),
    )


def _build_candle(item: dict[str, Any], timeframe: Timeframe, *, as_of: datetime) -> Candle | None:
    start = extract_timestamp(item)
    open_ = _as_float(_get_alias(item, _OPEN_ALIASES))
    high = _as_float(_get_alias(item, _HIGH_ALIASES))
    low = _as_float(_get_alias(item, _LOW_ALIASES))
    close = _as_float(_get_alias(item, _CLOSE_ALIASES))
    if start is None or open_ is None or high is None or low is None or close is None:
        return None
    end = start + timedelta(minutes=timeframe.minutes)
    volume = _as_float(_get_alias(item, _VOLUME_ALIASES))
    return Candle(
        timeframe=timeframe,
        start=start,
        end=end,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=end <= as_of,
        source="raw",
    )


def _dedupe_and_sort_candles(candles: list[Candle]) -> tuple[Candle, ...]:
    """Upstream can (rarely) resend a timestamp already seen - e.g. a
    revised/corrected bar, or a websocket replay glitch - or deliver bars
    out of order. One candle per `start` (last occurrence wins, since a
    later entry for the same period is the most likely to be the
    corrected/final version), sorted ascending, so every downstream
    consumer can rely on a clean one-bar-per-period series without each
    reimplementing this."""
    by_start: dict[datetime, Candle] = {}
    for c in candles:
        by_start[c.start] = c
    return tuple(sorted(by_start.values(), key=lambda c: c.start))


def _build_underlying(result: EndpointFetchResult | None, *, as_of: datetime) -> UnderlyingSnapshot | None:
    if result is None or result.data is None or not isinstance(result.data, dict):
        return None
    d = result.data
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    symbol = str(_get_alias(d, ("symbol", "underlying", "name")) or "")

    candles: dict[Timeframe, tuple[Candle, ...]] = {}
    for tf, aliases in _CANDLE_TIMEFRAME_ALIASES.items():
        raw_list = _get_alias(d, aliases)
        if isinstance(raw_list, list):
            built = [_build_candle(item, tf, as_of=as_of) for item in raw_list if isinstance(item, dict)]
            filtered = [c for c in built if c is not None and c.start <= as_of]
            deduped = _dedupe_and_sort_candles(filtered)
            if deduped:
                candles[tf] = deduped

    prev_day_raw = _get_alias(d, _PREV_DAY_ALIASES)
    prev_week_raw = _get_alias(d, _PREV_WEEK_ALIASES)

    return UnderlyingSnapshot(
        symbol=symbol,
        ltp=_sf(d, _LTP_ALIASES, source="underlying:ltp", **kw),
        day_ohlc=_build_ohlc(d, source_prefix="underlying:day", **kw),
        prev_day_ohlc=_build_ohlc(prev_day_raw, source_prefix="underlying:prev_day", **kw)
        if isinstance(prev_day_raw, dict)
        else None,
        prev_week_ohlc=_build_ohlc(prev_week_raw, source_prefix="underlying:prev_week", **kw)
        if isinstance(prev_week_raw, dict)
        else None,
        candles=candles,
    )


def _build_futures(result: EndpointFetchResult | None, *, as_of: datetime) -> FuturesSnapshot | None:
    if result is None or result.data is None:
        return None
    underlying = ""
    items = _extract_list(result.data)
    if not items and isinstance(result.data, dict):
        items = [result.data]
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)

    legs: list[FuturesLeg] = []
    for item in items:
        expiry = _as_date(_get_alias(item, _EXPIRY_ALIASES))
        legs.append(
            FuturesLeg(
                expiry=expiry,
                ltp=_sf(item, _LTP_ALIASES, source="futures:ltp", **kw),
                oi=_sf(item, _OI_ALIASES, source="futures:oi", **kw),
                oi_change=_sf(item, _OI_CHANGE_ALIASES, source="futures:oi_change", **kw),
                volume=_sf(item, _VOLUME_ALIASES, source="futures:volume", **kw),
                ohlc=_build_ohlc(item, source_prefix="futures:day", **kw),
            )
        )
    legs.sort(key=lambda leg: leg.expiry or date.max)
    return FuturesSnapshot(underlying=underlying, legs=tuple(legs))


def _oi_change_from_previous(leg_data: Mapping[str, Any], *, source: str, kw: dict[str, Any]) -> SourcedField[float]:
    """Real payload has `oi` and `previous_oi` but no direct oi-change field
    - both are genuine observed values from the same fetch, so computing
    their difference is arithmetic on real data, not a fabricated value."""
    oi = _as_float(_get_alias(leg_data, _OI_ALIASES))
    previous_oi = _as_float(_get_alias(leg_data, ("previous_oi",)))
    if oi is None or previous_oi is None:
        return SourcedField.missing(source)
    return SourcedField.of(oi - previous_oi, source=source, **kw)


def _build_option_leg(
    leg_data: Mapping[str, Any],
    *,
    security_id: Any,
    strike: float,
    option_type: OptionType,
    expiry: date | None,
    kw: dict[str, Any],
) -> OptionLeg:
    return OptionLeg(
        security_id=str(security_id or ""),
        symbol=str(_get_alias(leg_data, _SYMBOL_ALIASES) or ""),
        strike=strike,
        option_type=option_type,
        expiry=expiry,
        ltp=_sf(leg_data, _LTP_ALIASES, source="options:ltp", **kw),
        bid=_sf(leg_data, _BID_ALIASES, source="options:bid", **kw),
        ask=_sf(leg_data, _ASK_ALIASES, source="options:ask", **kw),
        volume=_sf(leg_data, _VOLUME_ALIASES, source="options:volume", **kw),
        oi=_sf(leg_data, _OI_ALIASES, source="options:oi", **kw),
        oi_change=_oi_change_from_previous(leg_data, source="options:oi_change", kw=kw),
        iv=_sf(leg_data, _IV_ALIASES, source="options:iv", **kw),
        delta=_sf(leg_data.get("greeks") or leg_data, _DELTA_ALIASES, source="options:delta", **kw),
        gamma=_sf(leg_data.get("greeks") or leg_data, _GAMMA_ALIASES, source="options:gamma", **kw),
        theta=_sf(leg_data.get("greeks") or leg_data, _THETA_ALIASES, source="options:theta", **kw),
        vega=_sf(leg_data.get("greeks") or leg_data, _VEGA_ALIASES, source="options:vega", **kw),
    )


def _build_options(
    result: EndpointFetchResult | None, *, underlying: str, as_of: datetime
) -> OptionChainSnapshot | None:
    """Verified against `artifacts/production_endpoint_samples.json`
    (2026-09-19): the real payload nests each strike as
    `{"strike": <float>, "ce": {...}, "pe": {...}}` inside a top-level
    `strikes` list, with `delta/gamma/theta/vega` further nested one level
    under `ce`/`pe`'s own `greeks` dict, and a single `expiry` (ISO date)
    shared by the whole chain at the payload's top level - there is no
    per-leg expiry field. This replaces the original guess of a flat list
    of legs each carrying their own `option_type`/`expiry`, which matched
    nothing in the real payload and silently produced zero legs.
    """
    if result is None or result.data is None or not isinstance(result.data, dict):
        return None
    payload = result.data
    chain_expiry = _as_date(_get_alias(payload, _EXPIRY_ALIASES))
    items = _extract_list(payload)
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)

    legs: list[OptionLeg] = []
    for item in items:
        strike = _as_float(_get_alias(item, _STRIKE_ALIASES))
        if strike is None:
            continue

        ce = item.get("ce")
        if isinstance(ce, dict):
            legs.append(
                _build_option_leg(
                    ce, security_id=ce.get("security_id"), strike=strike, option_type="CE", expiry=chain_expiry, kw=kw
                )
            )
        pe = item.get("pe")
        if isinstance(pe, dict):
            legs.append(
                _build_option_leg(
                    pe, security_id=pe.get("security_id"), strike=strike, option_type="PE", expiry=chain_expiry, kw=kw
                )
            )
    return OptionChainSnapshot(underlying=underlying, legs=tuple(legs))


def _build_depth_levels(raw: Any) -> tuple[DepthLevel, ...]:
    """Verified against artifacts/production_endpoint_samples.json
    (2026-09-19): the real depth payload always sends a fixed-length
    20-slot array per side, whether quoted or not - an empty book slot is
    `{"price": 0.0, "quantity": 0, "orders": 0}`, not simply absent. A
    price of 0 (or non-positive) is never a real resting order, so it is
    filtered out here rather than surfaced as a fabricated-looking
    `best_bid=0.0`/`best_ask=0.0` - a genuinely all-empty ladder must
    become an empty tuple so callers (options/selection.py) correctly
    treat it as "no real depth" and fall back to the option chain's own
    bid/ask, instead of trusting a hollow ladder as if it were real.
    """
    if not isinstance(raw, list):
        return ()
    levels: list[DepthLevel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        price = _as_float(_get_alias(item, ("price",)))
        qty = _as_float(_get_alias(item, ("quantity", "qty", "size")))
        if price is None or qty is None or price <= 0:
            continue
        orders_raw = _get_alias(item, ("orders", "num_orders"))
        orders = int(orders_raw) if isinstance(orders_raw, (int, float)) else None
        levels.append(DepthLevel(price=price, quantity=qty, orders=orders))
    return tuple(levels)


def _build_depth(result: EndpointFetchResult | None, *, underlying: str) -> DepthSnapshot | None:
    """Verified against `artifacts/production_endpoint_samples.json`
    (2026-09-19): the real payload's per-underlying container is a
    top-level `contracts` list (now in `_LIST_CONTAINER_ALIASES`), and each
    contract carries singular `bid`/`ask` keys (not `bids`/`asks`) holding
    a 20-level array of `{level, orders, price, quantity}` - `price`/
    `quantity`/`orders` matched the original guess. Each contract also
    carries its own `oi`/`volume`/`last_price`/`ohlc`/`strike`/
    `option_type`/`expiry`, which this function does not surface (depth is
    modeled as bid/ask microstructure only - see `domain/snapshot.py`
    `InstrumentDepth`); the `options` endpoint remains the source for
    those fields.
    """
    if result is None or result.data is None:
        return None
    data = result.data
    by_id: dict[str, InstrumentDepth] = {}

    entries: list[dict[str, Any]] = []
    if isinstance(data, list):
        entries = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        container = _extract_list(data)
        if container:
            entries = container
        else:
            # dict keyed directly by security_id -> {bids, asks}
            for key, value in data.items():
                if isinstance(value, dict) and ("bids" in _lower_map(value) or "asks" in _lower_map(value)):
                    entries.append({"security_id": key, **value})

    for entry in entries:
        security_id = str(_get_alias(entry, _SECURITY_ID_ALIASES) or "")
        if not security_id:
            continue
        bids_raw = _get_alias(entry, ("bid", "bids", "bid_levels", "buy"))
        asks_raw = _get_alias(entry, ("ask", "asks", "ask_levels", "sell"))
        by_id[security_id] = InstrumentDepth(
            security_id=security_id,
            bids=_build_depth_levels(bids_raw),
            asks=_build_depth_levels(asks_raw),
        )

    return DepthSnapshot(underlying=underlying, by_security_id=by_id)


def _build_breadth(result: EndpointFetchResult | None) -> BreadthSnapshot | None:
    """Verified against `artifacts/production_endpoint_samples.json`
    (2026-09-19): the real keys are `advancing`/`declining` (full words),
    not the originally-guessed `advances`/`declines` abbreviations;
    `unchanged` matched as guessed.
    """
    if result is None or result.data is None or not isinstance(result.data, dict):
        return None
    d = result.data
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    return BreadthSnapshot(
        advances=_sf(d, ("advancing", "advances", "advance", "adv"), source="breadth:advances", **kw),
        declines=_sf(d, ("declining", "declines", "decline", "dec"), source="breadth:declines", **kw),
        unchanged=_sf(d, ("unchanged", "unch"), source="breadth:unchanged", **kw),
    )


def _build_sectors(result: EndpointFetchResult | None) -> tuple[SectorSnapshot, ...]:
    if result is None or result.data is None:
        return ()
    items = _extract_list(result.data)
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    sectors: list[SectorSnapshot] = []
    for item in items:
        name = _get_alias(item, ("name", "sector", "sector_name"))
        if not name:
            continue
        sectors.append(
            SectorSnapshot(
                name=str(name),
                change_pct=_sf(item, ("change_pct", "change", "pct_change"), source=f"sectors:{name}", **kw),
            )
        )
    return tuple(sectors)


def _build_vix(result: EndpointFetchResult | None) -> VixSnapshot | None:
    if result is None or result.data is None or not isinstance(result.data, dict):
        return None
    d = result.data
    return VixSnapshot(
        value=_sf(
            d,
            ("value", "vix", *_LTP_ALIASES),
            source="india_vix:value",
            observed_at=result.observed_at,
            fetched_at=result.fetched_at,
        )
    )


def _build_global_context(result: EndpointFetchResult | None) -> tuple[GlobalContextSeries, ...]:
    """Verified against `artifacts/production_endpoint_samples.json`
    (2026-09-19): the real series (sp500, us_10y_yield, usd_inr, vix,
    wti_crude_oil in the sample) live nested under a top-level `series`
    dict, alongside sibling metadata keys (`market_data_status`,
    `not_available`, `refresh_seconds`, ...) that are not series and were
    previously being misread as one each. This replaces the original guess
    of the whole payload being a flat name->value map.
    """
    if result is None or result.data is None or not isinstance(result.data, dict):
        return ()
    payload = result.data
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    series_map = payload.get("series")
    if not isinstance(series_map, dict):
        return ()

    series: list[GlobalContextSeries] = []
    for name, value in series_map.items():
        if isinstance(value, dict):
            v = _sf(value, ("value", *_LTP_ALIASES), source=f"global_context:{name}", **kw)
            source_date = _as_date(_get_alias(value, ("source_date", "date", "as_of")))
        else:
            v = _sf({"value": value}, ("value",), source=f"global_context:{name}", **kw)
            source_date = None
        series.append(GlobalContextSeries(name=str(name), value=v, source_date=source_date))
    return tuple(series)


def _build_news(result: EndpointFetchResult | None) -> tuple[NewsItem, ...]:
    if result is None or result.data is None:
        return ()
    items = _extract_list(result.data)
    news: list[NewsItem] = []
    for item in items:
        headline = _get_alias(item, ("headline", "title"))
        if not headline:
            continue
        published_at = extract_timestamp(item)
        news.append(NewsItem(headline=str(headline), published_at=published_at, source="rbi_news"))
    return tuple(news)


def build_market_snapshot(
    bundle: RawFetchBundle, *, as_of: datetime, settings: Settings | None = None
) -> MarketSnapshot:
    """Build a `MarketSnapshot` from a fetched `RawFetchBundle`.

    `as_of` is the decision's information boundary — every timestamped
    element built here (candles, in particular) is filtered against it, so
    a caller replaying history can never leak a future bar even if the raw
    bundle happens to contain one (docs/ARCHITECTURE.md section 3).
    """
    settings = settings or get_settings()

    underlying_snapshot = _build_underlying(bundle.results.get("underlying"), as_of=as_of)
    options = _build_options(bundle.results.get("options"), underlying=bundle.underlying, as_of=as_of)
    depth = _build_depth(bundle.results.get("depth"), underlying=bundle.underlying)

    # "required canonical information is actually extractable" (a critical
    # endpoint can be a well-formed, fresh, structurally valid payload and
    # still extract to nothing usable - e.g. an options chain whose every
    # strike failed to parse into a leg). Deliberately NOT applied to
    # "depth": an empty/zero-level depth ladder is a legitimate per-
    # instrument liquidity fact with an intentional fallback
    # (options/selection.py falls back to the option chain's own bid/ask),
    # not a data-integrity failure - forcing it here would fight that
    # design rather than harden it. "depth is None" (the one genuine
    # extraction failure for this endpoint) is already caught by the
    # fetch-level MISSING/ERROR check in data/validation.py.
    critical_extraction_ok = {
        "underlying": (
            underlying_snapshot is not None
            and underlying_snapshot.ltp.available
            and underlying_snapshot.ltp.value is not None
        ),
        "options": options is not None and len(options.legs) > 0,
    }
    data_quality = build_data_quality(
        bundle, settings=settings, as_of=as_of, critical_extraction_ok=critical_extraction_ok
    )

    return MarketSnapshot(
        underlying=bundle.underlying,
        as_of=as_of,
        data_quality=data_quality,
        underlying_snapshot=underlying_snapshot,
        futures=_build_futures(bundle.results.get("futures"), as_of=as_of),
        options=options,
        depth=depth,
        breadth=_build_breadth(bundle.results.get("market_breadth")),
        sectors=_build_sectors(bundle.results.get("sectors")),
        vix=_build_vix(bundle.results.get("india_vix")),
        global_context=_build_global_context(bundle.results.get("global_context")),
        news=_build_news(bundle.results.get("rbi_news")),
    )
