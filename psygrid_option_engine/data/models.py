"""Raw fetch result types.

These wrap *whatever JSON the upstream actually returned* plus retrieval
metadata. They deliberately do not assume a specific per-endpoint field
shape (see docs/ENDPOINTS.md — the contract is unverified); typed,
per-endpoint canonical shapes are built on top of these in `domain/` from
Phase 3 onward, once the real contract is confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psygrid_option_engine.config.settings import EndpointCriticality


@dataclass(frozen=True)
class EndpointFetchResult:
    """Outcome of fetching one logical endpoint, success or failure alike.

    Exactly one of (`data` populated, `error` populated) is expected in
    normal operation, but both may be inspected independently — a
    structurally-invalid-but-present payload can have both `data` and
    `issues` populated with `error=None`.
    """

    logical_name: str
    url: str
    criticality: EndpointCriticality
    requested_at: datetime
    fetched_at: datetime | None
    latency_ms: float | None
    http_status: int | None
    data: Any | None
    observed_at: datetime | None  # timestamp extracted from the payload itself
    issues: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None


@dataclass(frozen=True)
class RawFetchBundle:
    """All endpoint results collected for one decision-cycle fetch."""

    underlying: str
    requested_at: datetime
    results: dict[str, EndpointFetchResult]

    def critical_results(self) -> dict[str, EndpointFetchResult]:
        return {
            k: v for k, v in self.results.items() if v.criticality is EndpointCriticality.CRITICAL
        }

    def optional_results(self) -> dict[str, EndpointFetchResult]:
        return {
            k: v for k, v in self.results.items() if v.criticality is EndpointCriticality.OPTIONAL
        }

    def all_critical_ok(self) -> bool:
        crit = self.critical_results()
        return bool(crit) and all(r.ok for r in crit.values())
