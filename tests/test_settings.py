from __future__ import annotations

from datetime import time

from psygrid_option_engine.config.settings import (
    EndpointCriticality,
    Settings,
    endpoint_criticality,
)


def test_base_url_trailing_slash_stripped() -> None:
    s = Settings(base_url="http://example.com/")
    assert s.base_url == "http://example.com"


def test_session_window_parses_overrides() -> None:
    s = Settings(market_open="09:20", entry_cutoff="14:45")
    window = s.session_window()
    assert window.market_open == time(9, 20)
    assert window.entry_cutoff == time(14, 45)


def test_endpoint_criticality_lookup() -> None:
    assert endpoint_criticality("underlying") is EndpointCriticality.CRITICAL
    assert endpoint_criticality("rbi_news") is EndpointCriticality.OPTIONAL


def test_endpoint_criticality_unregistered_raises() -> None:
    import pytest

    with pytest.raises(KeyError):
        endpoint_criticality("not_a_real_endpoint")


def test_supported_underlyings_default() -> None:
    assert Settings().supported_underlyings == ("NIFTY", "BANKNIFTY", "SENSEX")
