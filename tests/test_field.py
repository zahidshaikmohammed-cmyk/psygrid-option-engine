from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from psygrid_option_engine.domain.field import Freshness, SourcedField

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def test_missing_field_is_unavailable() -> None:
    f: SourcedField[float] = SourcedField.missing("nifty.json:ltp")
    assert f.available is False
    assert f.value is None
    assert f.freshness(NOW, 15.0) is Freshness.UNKNOWN
    assert f.age_seconds(NOW) is None


def test_zero_value_is_distinct_from_missing() -> None:
    # A delta of 0.0 or an OI change of 0 is a real value, not "missing".
    f = SourcedField.of(0.0, source="nifty-options.json:delta", observed_at=NOW, fetched_at=NOW)
    assert f.available is True
    assert f.value == 0.0


def test_require_raises_on_missing() -> None:
    f: SourcedField[float] = SourcedField.missing("x")
    with pytest.raises(ValueError):
        f.require()


def test_require_returns_value_when_available() -> None:
    f = SourcedField.of(24500.5, source="x", observed_at=NOW, fetched_at=NOW)
    assert f.require() == 24500.5


@pytest.mark.parametrize(
    "age_seconds,tolerance,expected",
    [
        (0.0, 15.0, Freshness.OK),
        (15.0, 15.0, Freshness.OK),  # boundary: exactly at tolerance is OK
        (15.001, 15.0, Freshness.STALE),
        (1000.0, 15.0, Freshness.STALE),
    ],
)
def test_freshness_boundary(age_seconds: float, tolerance: float, expected: Freshness) -> None:
    observed = NOW - timedelta(seconds=age_seconds)
    f = SourcedField.of(1.0, source="x", observed_at=observed, fetched_at=NOW)
    assert f.freshness(NOW, tolerance) is expected


def test_freshness_unknown_when_no_observed_at() -> None:
    f = SourcedField.of(1.0, source="x", observed_at=None, fetched_at=NOW)
    assert f.freshness(NOW, 15.0) is Freshness.UNKNOWN
