"""Structural validation, timestamp normalization, and freshness/data-quality
assessment for raw upstream payloads.

Per docs/ENDPOINTS.md the real field names are unverified, so structural
validation here is deliberately tolerant: it flags *issues* (recorded, not
fatal) for payloads that don't match the best-guess shape, and only treats
a payload as unusable if it isn't even a JSON object/array, or is outright
missing. Tightening this once the real contract is confirmed should mean
edits to `_STRUCTURAL_CHECKS` below, not a rewrite of the calling code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from psygrid_option_engine.config.settings import Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.signals.schema import (
    DataQuality,
    DataQualityOverall,
    SourceStatus,
    SourceStatusLiteral,
)

# Keys we'll look for, in order, to find a payload's own reported timestamp.
# "ltp_timestamp" confirmed present (though null while the market was
# closed) on the real underlying/india_vix payloads - see
# artifacts/production_endpoint_samples.json (2026-09-19).
_TIMESTAMP_KEYS = (
    "timestamp",
    "as_of",
    "asof",
    "observed_at",
    "updated_at",
    "last_updated",
    "ltp_timestamp",
    "source_date",
    "time",
    "date",
)

_IST = timezone(timedelta(hours=5, minutes=30))

# Container keys for list-shaped payloads nested under a dict. This is the
# ONE canonical list - both this module's structural validation and
# data/snapshot_builder.py's extraction import it, so they can never drift
# out of sync again. They did once: this module's own copy was missing
# "contracts", so a real, valid, HTTP-200 depth payload (container key
# "contracts" - verified against artifacts/production_endpoint_samples.json,
# 2026-09-19) was flagged with a false "no recognizable list container key
# found" structural issue even though snapshot_builder.py's separate list
# already knew about "contracts". "strikes"/"contracts"/"sectors" are
# verified real (options/depth/sectors payloads respectively); the rest are
# best-guess fallbacks kept for resilience against other shapes.
LIST_CONTAINER_KEYS = (
    "data",
    "results",
    "items",
    "chain",
    "strikes",
    "options",
    "records",
    "legs",
    "contracts",
    "sectors",
)

# Plausible price-field aliases for an underlying/futures snapshot.
_PRICE_FIELD_ALIASES = ("ltp", "last_price", "close", "ltp_price", "price", "last")


def extract_timestamp(payload: Any) -> datetime | None:
    """Best-effort extraction of a payload's self-reported timestamp,
    normalized to a UTC-aware datetime. Returns None if nothing plausible
    is found — callers must treat that as UNKNOWN freshness, not as "now"."""
    candidate = payload
    if isinstance(candidate, list):
        candidate = candidate[0] if candidate else None
    if not isinstance(candidate, dict):
        return None

    lowered = {str(k).lower(): v for k, v in candidate.items()}
    for key in _TIMESTAMP_KEYS:
        if key in lowered:
            parsed = _parse_timestamp_value(lowered[key])
            if parsed is not None:
                return parsed
    return None


def _parse_timestamp_value(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        # Heuristic: treat values > 10^12 as milliseconds, else seconds.
        seconds = value / 1000.0 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Confirmed real format (market_breadth.as_of, sectors.as_of,
        # underlying/india_vix session.current_time_ist):
        # "2026-09-20 00:11:38 IST" - not ISO-parseable as-is, and PSYGRID
        # only ever means Indian Standard Time by this suffix (its own
        # `session.timezone` field reports "Asia/Kolkata").
        if text.endswith(" IST"):
            try:
                naive = datetime.strptime(text[: -len(" IST")], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
            return naive.replace(tzinfo=_IST)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def validate_structure(logical_name: str, payload: Any) -> tuple[str, ...]:
    """Returns a tuple of human-readable issue strings; empty means no
    structural concerns found. Never raises on a shape mismatch — only an
    outright non-JSON-object/array payload is flagged as unusable."""
    issues: list[str] = []

    if payload is None:
        return ("payload is empty/None",)

    if not isinstance(payload, (dict, list)):
        return (f"unexpected top-level JSON type: {type(payload).__name__}",)

    checker = _STRUCTURAL_CHECKS.get(logical_name)
    if checker is not None:
        issues.extend(checker(payload))

    return tuple(issues)


def _check_underlying_shape(payload: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        issues.append("expected a JSON object for an underlying snapshot")
        return issues
    lowered = {str(k).lower() for k in payload}
    if not (lowered & set(_PRICE_FIELD_ALIASES)):
        issues.append(
            "no recognizable price field found "
            f"(looked for one of {_PRICE_FIELD_ALIASES}); "
            "verify docs/ENDPOINTS.md against the live payload"
        )
    return issues


def _check_options_shape(payload: Any) -> list[str]:
    issues: list[str] = []
    if isinstance(payload, list):
        return issues
    if isinstance(payload, dict):
        lowered = {str(k).lower(): k for k in payload}
        if not any(k in lowered for k in LIST_CONTAINER_KEYS):
            issues.append(
                "options payload is an object but no recognizable list "
                f"container key found (looked for one of {LIST_CONTAINER_KEYS})"
            )
        return issues
    issues.append("expected a JSON array or object for an option chain")
    return issues


_STRUCTURAL_CHECKS = {
    "underlying": _check_underlying_shape,
    "futures": _check_underlying_shape,
    "options": _check_options_shape,
    "depth": _check_options_shape,
}


_FRESHNESS_TOLERANCE_ATTR = {
    "underlying": "freshness_tolerance_underlying_seconds",
    "options": "freshness_tolerance_options_seconds",
    "depth": "freshness_tolerance_depth_seconds",
    "indicators": "freshness_tolerance_indicators_seconds",
    "futures": "freshness_tolerance_futures_seconds",
    "market_breadth": "freshness_tolerance_breadth_seconds",
    "sectors": "freshness_tolerance_breadth_seconds",
    "global_context": "freshness_tolerance_context_seconds",
    "rbi_news": "freshness_tolerance_news_seconds",
    "india_vix": "freshness_tolerance_vix_seconds",
    "live": "freshness_tolerance_underlying_seconds",
}


def freshness_tolerance_for(logical_name: str, settings: Settings) -> float:
    attr = _FRESHNESS_TOLERANCE_ATTR.get(logical_name, "freshness_tolerance_underlying_seconds")
    return getattr(settings, attr)


def assess_source_status(
    result: EndpointFetchResult, *, as_of: datetime, tolerance_seconds: float
) -> SourceStatus:
    if result.error is not None or result.data is None:
        return SourceStatus(
            fetched_at=result.fetched_at,
            observed_at=result.observed_at,
            age_seconds=None,
            available=False,
            status="MISSING" if result.http_status is None else "ERROR",
        )

    if result.issues:
        # HTTP succeeded and JSON parsed, but the payload doesn't match
        # even the deliberately-tolerant shape check (e.g. no recognizable
        # list container for an option chain). A structurally-invalid
        # critical payload must never be treated as healthy merely because
        # the fetch itself succeeded.
        return SourceStatus(
            fetched_at=result.fetched_at,
            observed_at=result.observed_at,
            age_seconds=None,
            available=True,
            status="ERROR",
        )

    age_seconds = None
    if result.observed_at is not None:
        age_seconds = (as_of - result.observed_at).total_seconds()

    status: SourceStatusLiteral
    if age_seconds is None:
        # No self-reported timestamp found; fall back to fetch recency so we
        # don't punish a payload merely for not exposing a timestamp field.
        status = "OK"
    elif age_seconds < 0:
        # The payload's self-reported observation time is AFTER this
        # decision's own information boundary (`as_of`) - an information-
        # boundary violation (docs/ARCHITECTURE.md section 3: a decision at
        # time t may only use data observed <= t), never a "very fresh"
        # reading. Must never be silently trusted as current.
        status = "ERROR"
    elif age_seconds > tolerance_seconds:
        status = "STALE"
    else:
        status = "OK"

    return SourceStatus(
        fetched_at=result.fetched_at,
        observed_at=result.observed_at,
        age_seconds=age_seconds,
        available=True,
        status=status,
    )


def build_data_quality(
    bundle: RawFetchBundle,
    *,
    settings: Settings,
    as_of: datetime,
    critical_extraction_ok: dict[str, bool] | None = None,
) -> DataQuality:
    """`critical_extraction_ok` (optional): logical_name -> whether the
    canonical `MarketSnapshot` piece built from that endpoint's payload
    actually carried the required information a decision needs (e.g. the
    underlying's LTP, at least one option leg) - a payload can be
    well-formed JSON, structurally valid, and fresh, yet still extract to
    nothing usable. `data/snapshot_builder.py::build_market_snapshot` is
    the only production caller and always supplies this, since only it
    has built the canonical pieces by the time data quality is assessed.
    Left `None` only by direct unit tests of this function that don't
    need the extraction check.
    """
    per_source: dict[str, SourceStatus] = {}
    stale_fields: list[str] = []
    unavailable_fields: list[str] = []

    for name, result in bundle.results.items():
        tolerance = freshness_tolerance_for(name, settings)
        status = assess_source_status(result, as_of=as_of, tolerance_seconds=tolerance)
        per_source[name] = status
        if status.status == "STALE":
            stale_fields.append(name)
        elif status.status in ("MISSING", "ERROR"):
            unavailable_fields.append(name)

    # Computed from the assessed per-source STATUS (not the raw fetch-level
    # `.ok`), so a critical endpoint that is stale, structurally invalid,
    # or future-dated correctly makes critical_endpoints_ok False - not
    # merely "HTTP 200 and JSON parsed".
    critical_names = set(bundle.critical_results())
    critical_ok = bool(critical_names) and all(per_source[name].status == "OK" for name in critical_names)

    if critical_ok and critical_extraction_ok:
        for name, extracted in critical_extraction_ok.items():
            if name in critical_names and not extracted:
                critical_ok = False
                if name not in unavailable_fields:
                    unavailable_fields.append(name)

    overall: DataQualityOverall
    if not critical_ok:
        overall = "INSUFFICIENT"
    elif stale_fields or unavailable_fields:
        overall = "DEGRADED"
    else:
        overall = "GOOD"

    return DataQuality(
        overall=overall,
        critical_endpoints_ok=critical_ok,
        stale_fields=stale_fields,
        unavailable_fields=unavailable_fields,
        per_source=per_source,
    )
