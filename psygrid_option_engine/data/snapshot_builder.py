"""Adapter: `RawFetchBundle` (raw upstream JSON + fetch metadata) ->
canonical `domain.snapshot.MarketSnapshot`.

THIS IS THE ONE MODULE THAT KNOWS ABOUT RAW UPSTREAM FIELD NAMES, and per
docs/ENDPOINTS.md those names are unverified (best-guess from the task
brief, not an inspected production payload). Every alias list below is
that best guess. When `artifacts/production_endpoint_samples.json` (or
equivalent) is available, this is the only file that needs correcting —
`domain/snapshot.py` and everything built on it does not change.

Design rule enforced throughout: a wrong guess about a field name must
degrade to "field unavailable" (a missing `SourcedField`), never raise and
never silently substitute a wrong value. `data_quality` (built the same
way as Phase 2) already tells downstream layers when critical data itself
is missing/stale/invalid; this module adds no new failure mode on top of
that for per-field misses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.validation import build_data_quality, extract_timestamp
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
_BID_ALIASES = ("bid", "bid_price", "best_bid")
_ASK_ALIASES = ("ask", "ask_price", "offer", "best_ask")
_STRIKE_ALIASES = ("strike", "strike_price")
_OPTION_TYPE_ALIASES = ("option_type", "type", "opt_type", "right")
_EXPIRY_ALIASES = ("expiry", "expiry_date", "expiryDate")
_SECURITY_ID_ALIASES = ("security_id", "securityid", "id", "token", "instrument_token")
_SYMBOL_ALIASES = ("symbol", "trading_symbol", "tradingsymbol")

_PREV_DAY_ALIASES = ("prev_day", "previous_day", "prevday", "prev_day_ohlc")
_PREV_WEEK_ALIASES = ("prev_week", "previous_week", "prevweek", "prev_week_ohlc")

_LIST_CONTAINER_ALIASES = ("data", "results", "items", "chain", "strikes", "options", "records", "legs")

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
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


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
            filtered = tuple(c for c in built if c is not None and c.start <= as_of)
            if filtered:
                candles[tf] = filtered

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


def _normalize_option_type(raw: Any) -> OptionType | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper()
    if text in ("CE", "CALL", "C"):
        return "CE"
    if text in ("PE", "PUT", "P"):
        return "PE"
    return None


def _build_options(
    result: EndpointFetchResult | None, *, underlying: str, as_of: datetime
) -> OptionChainSnapshot | None:
    if result is None or result.data is None:
        return None
    items = _extract_list(result.data)
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)

    legs: list[OptionLeg] = []
    for item in items:
        strike = _as_float(_get_alias(item, _STRIKE_ALIASES))
        option_type = _normalize_option_type(_get_alias(item, _OPTION_TYPE_ALIASES))
        if strike is None or option_type is None:
            continue
        legs.append(
            OptionLeg(
                security_id=str(_get_alias(item, _SECURITY_ID_ALIASES) or ""),
                symbol=str(_get_alias(item, _SYMBOL_ALIASES) or ""),
                strike=strike,
                option_type=option_type,
                expiry=_as_date(_get_alias(item, _EXPIRY_ALIASES)),
                ltp=_sf(item, _LTP_ALIASES, source="options:ltp", **kw),
                bid=_sf(item, _BID_ALIASES, source="options:bid", **kw),
                ask=_sf(item, _ASK_ALIASES, source="options:ask", **kw),
                volume=_sf(item, _VOLUME_ALIASES, source="options:volume", **kw),
                oi=_sf(item, _OI_ALIASES, source="options:oi", **kw),
                oi_change=_sf(item, _OI_CHANGE_ALIASES, source="options:oi_change", **kw),
                iv=_sf(item, _IV_ALIASES, source="options:iv", **kw),
                delta=_sf(item, _DELTA_ALIASES, source="options:delta", **kw),
                gamma=_sf(item, _GAMMA_ALIASES, source="options:gamma", **kw),
                theta=_sf(item, _THETA_ALIASES, source="options:theta", **kw),
                vega=_sf(item, _VEGA_ALIASES, source="options:vega", **kw),
            )
        )
    return OptionChainSnapshot(underlying=underlying, legs=tuple(legs))


def _build_depth_levels(raw: Any) -> tuple[DepthLevel, ...]:
    if not isinstance(raw, list):
        return ()
    levels: list[DepthLevel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        price = _as_float(_get_alias(item, ("price",)))
        qty = _as_float(_get_alias(item, ("quantity", "qty", "size")))
        if price is None or qty is None:
            continue
        orders_raw = _get_alias(item, ("orders", "num_orders"))
        orders = int(orders_raw) if isinstance(orders_raw, (int, float)) else None
        levels.append(DepthLevel(price=price, quantity=qty, orders=orders))
    return tuple(levels)


def _build_depth(result: EndpointFetchResult | None, *, underlying: str) -> DepthSnapshot | None:
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
        bids_raw = _get_alias(entry, ("bids", "bid_levels", "buy"))
        asks_raw = _get_alias(entry, ("asks", "ask_levels", "sell"))
        by_id[security_id] = InstrumentDepth(
            security_id=security_id,
            bids=_build_depth_levels(bids_raw),
            asks=_build_depth_levels(asks_raw),
        )

    return DepthSnapshot(underlying=underlying, by_security_id=by_id)


def _build_breadth(result: EndpointFetchResult | None) -> BreadthSnapshot | None:
    if result is None or result.data is None or not isinstance(result.data, dict):
        return None
    d = result.data
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    return BreadthSnapshot(
        advances=_sf(d, ("advances", "advance", "adv"), source="breadth:advances", **kw),
        declines=_sf(d, ("declines", "decline", "dec"), source="breadth:declines", **kw),
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
    if result is None or result.data is None:
        return ()
    d = result.data
    kw = dict(observed_at=result.observed_at, fetched_at=result.fetched_at)
    series: list[GlobalContextSeries] = []

    if isinstance(d, dict):
        for name, value in d.items():
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
    data_quality = build_data_quality(bundle, settings=settings, as_of=as_of)

    return MarketSnapshot(
        underlying=bundle.underlying,
        as_of=as_of,
        data_quality=data_quality,
        underlying_snapshot=_build_underlying(bundle.results.get("underlying"), as_of=as_of),
        futures=_build_futures(bundle.results.get("futures"), as_of=as_of),
        options=_build_options(bundle.results.get("options"), underlying=bundle.underlying, as_of=as_of),
        depth=_build_depth(bundle.results.get("depth"), underlying=bundle.underlying),
        breadth=_build_breadth(bundle.results.get("market_breadth")),
        sectors=_build_sectors(bundle.results.get("sectors")),
        vix=_build_vix(bundle.results.get("india_vix")),
        global_context=_build_global_context(bundle.results.get("global_context")),
        news=_build_news(bundle.results.get("rbi_news")),
    )
