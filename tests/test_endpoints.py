from __future__ import annotations

import pytest

from psygrid_option_engine.data.endpoints import ENDPOINT_REGISTRY, stock_endpoint_path


@pytest.mark.parametrize(
    "logical_name,underlying,expected",
    [
        ("underlying", "NIFTY", "/public/nifty.json"),
        ("underlying", "BANKNIFTY", "/public/banknifty.json"),
        ("options", "NIFTY", "/public/nifty-options.json"),
        ("options", "BANKNIFTY", "/public/banknifty-options.json"),
        ("depth", "NIFTY", "/public/nifty-depth.json"),
        ("indicators", "BANKNIFTY", "/public/banknifty-indicators.json"),
        ("futures", "NIFTY", "/public/nifty-futures.json"),
    ],
)
def test_per_underlying_paths(logical_name: str, underlying: str, expected: str) -> None:
    assert ENDPOINT_REGISTRY[logical_name].path(underlying) == expected


@pytest.mark.parametrize(
    "logical_name,expected",
    [
        ("market_breadth", "/public/market-breadth.json"),
        ("sectors", "/public/sectors.json"),
        ("global_context", "/public/global-context.json"),
        ("rbi_news", "/public/rbi-news.json"),
        ("india_vix", "/public/indiavix.json"),
        ("live", "/public/live.json"),
    ],
)
def test_global_paths(logical_name: str, expected: str) -> None:
    assert ENDPOINT_REGISTRY[logical_name].path() == expected


def test_per_underlying_endpoint_requires_underlying() -> None:
    with pytest.raises(ValueError):
        ENDPOINT_REGISTRY["underlying"].path(None)


def test_unsupported_underlying_rejected() -> None:
    with pytest.raises(ValueError):
        ENDPOINT_REGISTRY["underlying"].path("SENSEX")


def test_stock_endpoint_path() -> None:
    assert stock_endpoint_path("RELIANCE") == "/public/stock/RELIANCE.json"
