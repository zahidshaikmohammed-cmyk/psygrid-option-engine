from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.validation import (
    build_data_quality,
    extract_timestamp,
    validate_structure,
)

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"ltp": 100, "timestamp": "2026-09-18T05:00:00Z"}, datetime(2026, 9, 18, 5, 0, tzinfo=UTC)),
        ({"ltp": 100, "timestamp": "2026-09-18T10:30:00+05:30"}, datetime(2026, 9, 18, 5, 0, tzinfo=UTC)),
        ({"ltp": 100, "as_of": 1789800000}, datetime.fromtimestamp(1789800000, tz=UTC)),
        ({"ltp": 100, "updated_at": 1789800000000}, datetime.fromtimestamp(1789800000, tz=UTC)),
        ({"ltp": 100}, None),
        ([], None),
        (None, None),
        ([{"timestamp": "2026-09-18T05:00:00Z"}], datetime(2026, 9, 18, 5, 0, tzinfo=UTC)),
    ],
)
def test_extract_timestamp(payload: object, expected: datetime | None) -> None:
    assert extract_timestamp(payload) == expected


def test_extract_timestamp_malformed_string_ignored() -> None:
    assert extract_timestamp({"timestamp": "not-a-date"}) is None


def test_extract_timestamp_empty_string_ignored() -> None:
    assert extract_timestamp({"timestamp": ""}) is None


class TestValidateStructure:
    def test_none_payload(self) -> None:
        assert validate_structure("underlying", None) == ("payload is empty/None",)

    def test_wrong_top_level_type(self) -> None:
        issues = validate_structure("underlying", "not json")
        assert issues and "unexpected top-level JSON type" in issues[0]

    def test_underlying_with_recognizable_price_field_ok(self) -> None:
        assert validate_structure("underlying", {"ltp": 100}) == ()

    def test_underlying_missing_price_field_flagged(self) -> None:
        issues = validate_structure("underlying", {"foo": "bar"})
        assert issues and "no recognizable price field" in issues[0]

    def test_options_as_list_ok(self) -> None:
        assert validate_structure("options", [{"strike": 100}]) == ()

    def test_options_as_dict_with_container_key_ok(self) -> None:
        assert validate_structure("options", {"data": []}) == ()

    def test_options_as_dict_without_container_key_flagged(self) -> None:
        issues = validate_structure("options", {"foo": "bar"})
        assert issues and "no recognizable list container key" in issues[0]

    def test_unregistered_logical_name_no_checks(self) -> None:
        # No structural checker registered for e.g. "rbi_news" -> no issues
        # raised purely because of shape; this is intentional (section 20 of
        # the brief treats it as loosely-typed context, not a hard schema).
        assert validate_structure("rbi_news", {"anything": True}) == ()


def _result(
    logical_name: str,
    *,
    criticality: EndpointCriticality,
    data: object = {"ltp": 100},
    observed_at: datetime | None = NOW,
    error: str | None = None,
) -> EndpointFetchResult:
    return EndpointFetchResult(
        logical_name=logical_name,
        url=f"/public/{logical_name}.json",
        criticality=criticality,
        requested_at=NOW,
        fetched_at=NOW if error is None else None,
        latency_ms=5.0,
        http_status=200 if error is None else None,
        data=None if error else data,
        observed_at=None if error else observed_at,
        issues=(),
        error=error,
    )


def test_build_data_quality_good() -> None:
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=NOW,
        results={
            "underlying": _result("underlying", criticality=EndpointCriticality.CRITICAL),
            "options": _result("options", criticality=EndpointCriticality.CRITICAL, data=[]),
            "depth": _result("depth", criticality=EndpointCriticality.CRITICAL, data=[]),
        },
    )
    dq = build_data_quality(bundle, settings=Settings(), as_of=NOW)
    assert dq.overall == "GOOD"
    assert dq.critical_endpoints_ok is True
    assert dq.stale_fields == []
    assert dq.unavailable_fields == []


def test_build_data_quality_insufficient_on_critical_failure() -> None:
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=NOW,
        results={
            "underlying": _result("underlying", criticality=EndpointCriticality.CRITICAL),
            "options": _result(
                "options", criticality=EndpointCriticality.CRITICAL, error="HTTP 503"
            ),
            "depth": _result("depth", criticality=EndpointCriticality.CRITICAL, data=[]),
        },
    )
    dq = build_data_quality(bundle, settings=Settings(), as_of=NOW)
    assert dq.overall == "INSUFFICIENT"
    assert dq.critical_endpoints_ok is False
    assert "options" in dq.unavailable_fields


def test_build_data_quality_degraded_on_stale_optional() -> None:
    stale_observed = NOW - timedelta(seconds=1000)
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=NOW,
        results={
            "underlying": _result("underlying", criticality=EndpointCriticality.CRITICAL),
            "options": _result("options", criticality=EndpointCriticality.CRITICAL, data=[]),
            "depth": _result("depth", criticality=EndpointCriticality.CRITICAL, data=[]),
            "indicators": _result(
                "indicators",
                criticality=EndpointCriticality.OPTIONAL,
                data={"ema9": 1},
                observed_at=stale_observed,
            ),
        },
    )
    dq = build_data_quality(bundle, settings=Settings(), as_of=NOW)
    assert dq.overall == "DEGRADED"
    assert dq.critical_endpoints_ok is True
    assert "indicators" in dq.stale_fields


def test_build_data_quality_degraded_on_missing_optional() -> None:
    bundle = RawFetchBundle(
        underlying="NIFTY",
        requested_at=NOW,
        results={
            "underlying": _result("underlying", criticality=EndpointCriticality.CRITICAL),
            "options": _result("options", criticality=EndpointCriticality.CRITICAL, data=[]),
            "depth": _result("depth", criticality=EndpointCriticality.CRITICAL, data=[]),
            "india_vix": _result(
                "india_vix", criticality=EndpointCriticality.OPTIONAL, error="timeout"
            ),
        },
    )
    dq = build_data_quality(bundle, settings=Settings(), as_of=NOW)
    assert dq.overall == "DEGRADED"
    assert "india_vix" in dq.unavailable_fields
