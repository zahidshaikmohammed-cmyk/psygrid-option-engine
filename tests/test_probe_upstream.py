"""Tests for scripts/probe_upstream.py's redaction and helper logic.

This is a diagnostic/ops script, not part of the installed package, so it
is loaded here via its file path rather than a normal import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "probe_upstream.py"
_spec = importlib.util.spec_from_file_location("probe_upstream", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
probe_upstream = importlib.util.module_from_spec(_spec)
sys.modules["probe_upstream"] = probe_upstream
_spec.loader.exec_module(probe_upstream)


def test_redact_by_key_name() -> None:
    body = {"api_key": "anything", "Dhan_Token": "anything", "ltp": 100}
    redacted = probe_upstream._redact(body)
    assert redacted["api_key"] == probe_upstream.REDACTED
    assert redacted["Dhan_Token"] == probe_upstream.REDACTED
    assert redacted["ltp"] == 100


def test_redact_nested_structures() -> None:
    body = {"options": [{"greeks": {"secret": "x"}, "strike": 100}]}
    redacted = probe_upstream._redact(body)
    assert redacted["options"][0]["greeks"]["secret"] == probe_upstream.REDACTED
    assert redacted["options"][0]["strike"] == 100


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8", True),  # 37-char token-shaped
        ("2026-09-19T10:00:00Z", False),  # timestamp, not a secret
        ("2026-09-19T10:00:00+05:30", False),
        ("NIFTY25SEP24500CE", False),  # short symbol
        ("short", False),
        ("has spaces even though it is quite long indeed", False),
        ("Bearer abc.def.ghi", True),
    ],
)
def test_looks_like_secret_value(value: str, expected: bool) -> None:
    assert probe_upstream._looks_like_secret_value(value) is expected


def test_redact_by_value_shape_under_innocuous_key() -> None:
    # A bare token-shaped value under an innocuous key must still be
    # redacted, even though the key name itself gives no hint.
    body = {"note": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"}
    redacted = probe_upstream._redact(body)
    assert redacted["note"] == probe_upstream.REDACTED


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/public/nifty.json", "underlying"),
        ("/public/banknifty.json", "underlying"),
        ("/public/sensex.json", "underlying"),
        ("/public/nifty-options.json", "options"),
        ("/public/banknifty-depth.json", "depth"),
        ("/public/sensex-depth.json", "depth"),
        ("/public/nifty-indicators.json", "indicators"),
        ("/public/nifty-futures.json", "futures"),
        ("/public/market-breadth.json", "market_breadth"),
        ("/public/indiavix.json", "indiavix"),
    ],
)
def test_logical_guess(path: str, expected: str) -> None:
    assert probe_upstream._logical_guess(path) == expected


def test_registry_paths_includes_sensex() -> None:
    # Derived from data/endpoints.py's UNDERLYING_SLUGS (single source of
    # truth) rather than a separately-hardcoded underlying list here - a
    # previously-hardcoded copy of this list is exactly what caused
    # SENSEX to be silently skipped until this was fixed.
    paths = probe_upstream._registry_paths()
    assert paths["underlying_sensex"] == "/public/sensex.json"
    assert paths["options_sensex"] == "/public/sensex-options.json"
    assert paths["depth_sensex"] == "/public/sensex-depth.json"
    assert paths["indicators_sensex"] == "/public/sensex-indicators.json"
    assert paths["futures_sensex"] == "/public/sensex-futures.json"


def test_redact_headers_allowlist() -> None:
    import httpx

    headers = httpx.Headers(
        {"date": "x", "content-type": "application/json", "set-cookie": "secret=1"}
    )
    redacted = probe_upstream._redact_headers(headers)
    assert "date" in redacted
    assert "content-type" in redacted
    assert "set-cookie" not in redacted


def test_classify_market_status_market_open_bool_true() -> None:
    assert probe_upstream._classify_market_status({"market_open": True}) == "MARKET_OPEN"


def test_classify_market_status_market_open_bool_false() -> None:
    assert probe_upstream._classify_market_status({"market_open": False}) == "MARKET_CLOSED_EXPECTED"


def test_classify_market_status_market_status_string() -> None:
    assert probe_upstream._classify_market_status({"market_status": "CLOSED"}) == "MARKET_CLOSED"


def test_classify_market_status_nested_session_status() -> None:
    body = {"session": {"status": "CLOSED", "timezone": "Asia/Kolkata"}}
    assert probe_upstream._classify_market_status(body) == "MARKET_CLOSED"


def test_classify_market_status_nested_session_status_open() -> None:
    body = {"session": {"status": "OPEN"}}
    assert probe_upstream._classify_market_status(body) == "MARKET_OPEN"


def test_classify_market_status_none_when_no_recognizable_field() -> None:
    assert probe_upstream._classify_market_status({"advancing": 100, "declining": 50}) is None


def test_classify_market_status_none_for_non_dict_body() -> None:
    assert probe_upstream._classify_market_status([1, 2, 3]) is None


def test_classify_market_status_real_underlying_sample() -> None:
    # Real shape verified against artifacts/production_endpoint_samples.json
    # (2026-09-19).
    body = {"symbol": "NIFTY", "ltp": None, "session": {"status": "CLOSED", "timezone": "Asia/Kolkata"}}
    assert probe_upstream._classify_market_status(body) == "MARKET_CLOSED"


def test_classify_market_status_real_options_sample() -> None:
    body = {"market_open": False, "market_status": "CLOSED", "status": "LIVE"}
    assert probe_upstream._classify_market_status(body) == "MARKET_CLOSED_EXPECTED"
