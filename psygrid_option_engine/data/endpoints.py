"""Registry of PSYGRID upstream endpoints. The single source of truth for
URL paths — no other module may hard-code a `/public/...` path string. See
docs/ENDPOINTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from psygrid_option_engine.config.settings import EndpointCriticality, endpoint_criticality


class EndpointScope(StrEnum):
    PER_UNDERLYING = "PER_UNDERLYING"  # one instance per NIFTY/BANKNIFTY/SENSEX
    GLOBAL = "GLOBAL"  # fetched once, shared across underlyings


# SENSEX paths (/public/sensex.json, -options/-depth/-indicators/-futures)
# confirmed live by the user directly (2026-09-20) - same path convention as
# NIFTY/BANKNIFTY, so the same {slug}-based templates below apply unchanged.
# The payload *shape* for SENSEX has not been probed/verified yet (unlike
# NIFTY/BANKNIFTY, corrected against a real captured sample) - see
# docs/ENDPOINTS.md.
#
# Public (no leading underscore) so scripts/probe_upstream.py can derive its
# own per-underlying path list from this instead of maintaining a second,
# separately-hardcoded copy - exactly that kind of drift (two lists meant to
# stay in sync but didn't) already caused a real bug once in this codebase
# (see data/validation.py's LIST_CONTAINER_KEYS docstring).
UNDERLYING_SLUGS = {"NIFTY": "nifty", "BANKNIFTY": "banknifty", "SENSEX": "sensex"}


@dataclass(frozen=True)
class Endpoint:
    logical_name: str
    scope: EndpointScope
    criticality: EndpointCriticality
    path_template: str  # may reference {slug} for per-underlying endpoints

    def path(self, underlying: str | None = None) -> str:
        if self.scope is EndpointScope.PER_UNDERLYING:
            if underlying is None:
                raise ValueError(f"{self.logical_name} requires an underlying")
            slug = UNDERLYING_SLUGS.get(underlying)
            if slug is None:
                raise ValueError(f"Unsupported underlying {underlying!r}")
            return self.path_template.format(slug=slug)
        return self.path_template


def _endpoint(logical_name: str, scope: EndpointScope, path_template: str) -> Endpoint:
    return Endpoint(
        logical_name=logical_name,
        scope=scope,
        criticality=endpoint_criticality(logical_name),
        path_template=path_template,
    )


ENDPOINT_REGISTRY: dict[str, Endpoint] = {
    e.logical_name: e
    for e in (
        _endpoint("underlying", EndpointScope.PER_UNDERLYING, "/public/{slug}.json"),
        _endpoint("options", EndpointScope.PER_UNDERLYING, "/public/{slug}-options.json"),
        _endpoint("depth", EndpointScope.PER_UNDERLYING, "/public/{slug}-depth.json"),
        _endpoint("indicators", EndpointScope.PER_UNDERLYING, "/public/{slug}-indicators.json"),
        _endpoint("futures", EndpointScope.PER_UNDERLYING, "/public/{slug}-futures.json"),
        _endpoint("market_breadth", EndpointScope.GLOBAL, "/public/market-breadth.json"),
        _endpoint("sectors", EndpointScope.GLOBAL, "/public/sectors.json"),
        _endpoint("global_context", EndpointScope.GLOBAL, "/public/global-context.json"),
        _endpoint("rbi_news", EndpointScope.GLOBAL, "/public/rbi-news.json"),
        _endpoint("india_vix", EndpointScope.GLOBAL, "/public/indiavix.json"),
        _endpoint("live", EndpointScope.GLOBAL, "/public/live.json"),
    )
}


def per_underlying_endpoints() -> list[Endpoint]:
    return [e for e in ENDPOINT_REGISTRY.values() if e.scope is EndpointScope.PER_UNDERLYING]


def global_endpoints() -> list[Endpoint]:
    return [e for e in ENDPOINT_REGISTRY.values() if e.scope is EndpointScope.GLOBAL]


def stock_endpoint_path(symbol: str) -> str:
    """`/public/stock/{symbol}.json` — not in ENDPOINT_REGISTRY since it is
    parametrized by arbitrary symbol, not underlying; optional/best-effort
    per docs/ENDPOINTS.md."""
    return f"/public/stock/{symbol}.json"
