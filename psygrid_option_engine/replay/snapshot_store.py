"""Deterministic storage for captured `RawFetchBundle`s (brief section 32).

Stores the *raw* fetch results, not the derived `MarketSnapshot` - replay
rebuilds the snapshot via the same `data/snapshot_builder.py` the live path
uses, so a captured record is exactly what a live fetch would have
produced at that instant, nothing more.

Format: one JSON object per line (JSONL), appended in capture order.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from psygrid_option_engine.config.settings import EndpointCriticality
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle


def _dt_to_str(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _dt_from_str(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s is not None else None


def fetch_result_to_dict(result: EndpointFetchResult) -> dict[str, Any]:
    return {
        "logical_name": result.logical_name,
        "url": result.url,
        "criticality": result.criticality.value,
        "requested_at": _dt_to_str(result.requested_at),
        "fetched_at": _dt_to_str(result.fetched_at),
        "latency_ms": result.latency_ms,
        "http_status": result.http_status,
        "data": result.data,
        "observed_at": _dt_to_str(result.observed_at),
        "issues": list(result.issues),
        "error": result.error,
    }


def fetch_result_from_dict(d: dict[str, Any]) -> EndpointFetchResult:
    requested_at = _dt_from_str(d["requested_at"])
    assert requested_at is not None
    return EndpointFetchResult(
        logical_name=d["logical_name"],
        url=d["url"],
        criticality=EndpointCriticality(d["criticality"]),
        requested_at=requested_at,
        fetched_at=_dt_from_str(d["fetched_at"]),
        latency_ms=d["latency_ms"],
        http_status=d["http_status"],
        data=d["data"],
        observed_at=_dt_from_str(d["observed_at"]),
        issues=tuple(d["issues"]),
        error=d["error"],
    )


def bundle_to_dict(bundle: RawFetchBundle) -> dict[str, Any]:
    return {
        "underlying": bundle.underlying,
        "requested_at": _dt_to_str(bundle.requested_at),
        "results": {name: fetch_result_to_dict(r) for name, r in bundle.results.items()},
    }


def bundle_from_dict(d: dict[str, Any]) -> RawFetchBundle:
    requested_at = _dt_from_str(d["requested_at"])
    assert requested_at is not None
    return RawFetchBundle(
        underlying=d["underlying"],
        requested_at=requested_at,
        results={name: fetch_result_from_dict(r) for name, r in d["results"].items()},
    )


def append_bundle(path: Path, bundle: RawFetchBundle) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bundle_to_dict(bundle), sort_keys=True))
        f.write("\n")


def iter_bundles(path: Path) -> Iterator[RawFetchBundle]:
    """Yields bundles in file order. Callers needing strict chronological
    order should sort by `.requested_at` themselves (`replay/engine.py`
    does this) - file order is capture order, which is normally
    chronological but this function does not assume it."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield bundle_from_dict(json.loads(line))


def read_bundles(path: Path) -> list[RawFetchBundle]:
    return list(iter_bundles(path))
