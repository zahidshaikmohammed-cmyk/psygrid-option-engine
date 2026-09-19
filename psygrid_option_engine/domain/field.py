"""`SourcedField[T]`: the atomic data-quality-aware value wrapper.

Per docs/ARCHITECTURE.md section 3/5, almost no bare numeric value should
flow from `data/` into decision logic. Wrapping every value in a
`SourcedField` makes freshness and availability a first-class, uniformly
checkable property instead of something each layer re-derives ad hoc.

Design notes:
- `available=False` is distinct from `value=0.0`/`value=None` being a
  legitimate value (e.g. a delta of 0.0, or an OI change of 0). Never infer
  availability from "value is falsy".
- Freshness is computed relative to an explicit `as_of` instant passed by
  the caller (normally the snapshot's information-boundary time), not
  `datetime.now()`, so freshness evaluation stays deterministic and
  replay-safe (docs/ARCHITECTURE.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class Freshness(StrEnum):
    OK = "OK"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"  # no observed_at available to judge freshness


@dataclass(frozen=True)
class SourcedField(Generic[T]):
    """A value plus everything needed to judge whether to trust it."""

    value: T | None
    available: bool
    source: str  # logical endpoint/field path, e.g. "nifty.json:ltp"
    observed_at: datetime | None  # upstream-reported timestamp for this value
    fetched_at: datetime | None  # when this engine retrieved the payload

    @classmethod
    def missing(cls, source: str) -> SourcedField[T]:
        return cls(value=None, available=False, source=source, observed_at=None, fetched_at=None)

    @classmethod
    def of(
        cls,
        value: T,
        *,
        source: str,
        observed_at: datetime | None,
        fetched_at: datetime | None,
    ) -> SourcedField[T]:
        return cls(
            value=value,
            available=True,
            source=source,
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

    def age_seconds(self, as_of: datetime) -> float | None:
        if self.observed_at is None:
            return None
        return (as_of - self.observed_at).total_seconds()

    def freshness(self, as_of: datetime, tolerance_seconds: float) -> Freshness:
        if not self.available:
            return Freshness.UNKNOWN
        age = self.age_seconds(as_of)
        if age is None:
            return Freshness.UNKNOWN
        return Freshness.OK if age <= tolerance_seconds else Freshness.STALE

    def require(self) -> T:
        """Return the value or raise; use only where absence is already
        proven impossible (e.g. after an explicit availability check)."""
        if not self.available or self.value is None:
            raise ValueError(f"SourcedField {self.source!r} is not available")
        return self.value
