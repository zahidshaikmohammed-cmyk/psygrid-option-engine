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

# Plausible container keys for list-shaped payloads nested under a dict.
_LIST_CONTAINER_KEYS = ("data", "results", "items", "chain", "strikes", "options", "records")

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
        if not any(k in lowered for k in _LIST_CONTAINER_KEYS):
            issues.append(
                "options payload is an object but no recognizable list "
                f"container key found (looked for one of {_LIST_CONTAINER_KEYS})"
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

    age_seconds = None
    if result.observed_at is not None:
        age_seconds = (as_of - result.observed_at).total_seconds()

    status: SourceStatusLiteral
    if age_seconds is None:
        # No self-reported timestamp found; fall back to fetch recency so we
        # don't punish a payload merely for not exposing a timestamp field.
        status = "OK"
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
    bundle: RawFetchBundle, *, settings: Settings, as_of: datetime
) -> DataQuality:
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

    critical_ok = bundle.all_critical_ok()
    critical_stale = any(
        per_source[name].status == "STALE" for name in bundle.critical_results()
    )

    overall: DataQualityOverall
    if not critical_ok:
        overall = "INSUFFICIENT"
    elif critical_stale or stale_fields or unavailable_fields:
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
