from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from psygrid_option_engine.api.runtime import EngineRuntime
from psygrid_option_engine.api.state_machine import EngineState
from psygrid_option_engine.config.settings import Settings
from psygrid_option_engine.data.client import PsygridClient
from psygrid_option_engine.data.exceptions import ConfigurationError

from .conftest import OUTSIDE_SESSION_UTC, WITHIN_SESSION_UTC


def _healthy_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("-options.json"):
        return httpx.Response(200, json={"data": [{"strike": 24500}]})
    if path.endswith("-depth.json"):
        return httpx.Response(200, json={"data": []})
    if path in ("/public/nifty.json", "/public/banknifty.json"):
        return httpx.Response(200, json={"ltp": 100, "timestamp": "2026-09-18T05:00:00Z"})
    return httpx.Response(200, json={})


def _make_runtime(settings: Settings, handler) -> EngineRuntime:
    client = PsygridClient(settings, transport=httpx.MockTransport(handler))
    return EngineRuntime(settings, client=client)


def test_market_closed_returns_session_complete_without_network_call(fast_settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    runtime = _make_runtime(fast_settings, handler)
    result = runtime.run_cycle("NIFTY", now=OUTSIDE_SESSION_UTC)

    assert result.state is EngineState.SESSION_COMPLETE
    assert result.signal is None
    assert calls["n"] == 0  # no fetch attempted outside the session window
    runtime.close()


def test_market_open_with_healthy_data_reaches_market_analysis(fast_settings: Settings) -> None:
    runtime = _make_runtime(fast_settings, _healthy_handler)
    result = runtime.run_cycle("NIFTY", now=WITHIN_SESSION_UTC)

    assert result.state is EngineState.MARKET_ANALYSIS
    assert result.signal is None
    assert result.data_quality is not None
    assert result.data_quality.critical_endpoints_ok is True
    runtime.close()


def test_session_cutoff_still_allows_data_loading_but_flags_no_new_entries(
    fast_settings: Settings,
) -> None:
    # POST_ENTRY_CUTOFF (>= 15:00 IST) is still "within session" (positions
    # may still be open / monitored) even though no *new* entries should be
    # authorized; that authorization-level cutoff enforcement is Phase 5/9,
    # but the session-window primitive itself must distinguish the two.
    from datetime import datetime

    post_cutoff = datetime(2026, 9, 18, 9, 45, tzinfo=UTC)  # 15:15 IST
    runtime = _make_runtime(fast_settings, _healthy_handler)
    result = runtime.run_cycle("NIFTY", now=post_cutoff)

    assert result.state is EngineState.MARKET_ANALYSIS

    window = fast_settings.session_window()
    assert window.is_within_session(post_cutoff) is True
    assert window.is_new_entry_allowed(post_cutoff) is False
    runtime.close()


def test_critical_endpoint_failure_produces_no_trade_with_reasons(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "options" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"ltp": 100, "data": []})

    runtime = _make_runtime(fast_settings, handler)
    result = runtime.run_cycle("NIFTY", now=WITHIN_SESSION_UTC)

    assert result.state is EngineState.NO_TRADE
    assert result.signal is not None
    assert result.signal.state == "NO_TRADE"
    assert any("options" in r for r in result.signal.reasons)
    runtime.close()


def test_unsupported_underlying_raises_configuration_error(fast_settings: Settings) -> None:
    runtime = _make_runtime(fast_settings, _healthy_handler)
    with pytest.raises(ConfigurationError):
        runtime.run_cycle("SENSEX", now=WITHIN_SESSION_UTC)
    runtime.close()


def test_banknifty_supported(fast_settings: Settings) -> None:
    runtime = _make_runtime(fast_settings, _healthy_handler)
    result = runtime.run_cycle("BANKNIFTY", now=WITHIN_SESSION_UTC)
    assert result.state is EngineState.MARKET_ANALYSIS
    runtime.close()


def test_runtime_context_manager_closes_owned_client(fast_settings: Settings) -> None:
    injected_client = PsygridClient(fast_settings, transport=httpx.MockTransport(_healthy_handler))
    with EngineRuntime(fast_settings, client=injected_client) as runtime:
        runtime.run_cycle("NIFTY", now=WITHIN_SESSION_UTC)
    # owns_client is False here since a client was explicitly injected, so
    # closing the runtime must NOT close a client the caller might reuse.
    assert runtime._client._client.is_closed is False
