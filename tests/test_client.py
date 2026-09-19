from __future__ import annotations

import httpx
import pytest

from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.client import PsygridClient
from psygrid_option_engine.data.exceptions import UnregisteredEndpointError


def test_successful_fetch_parses_and_stamps(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public/nifty.json"
        return httpx.Response(200, json={"ltp": 100, "timestamp": "2026-09-18T05:00:00Z"})

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is True
    assert result.error is None
    assert result.http_status == 200
    assert result.data == {"ltp": 100, "timestamp": "2026-09-18T05:00:00Z"}
    assert result.observed_at is not None
    assert result.issues == ()
    assert result.criticality is EndpointCriticality.CRITICAL
    client.close()


def test_malformed_json_captured_not_raised(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is False
    assert "invalid JSON" in result.error
    client.close()


def test_structurally_odd_but_parseable_payload_flagged_not_failed(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is True  # data is present; this is a soft issue, not a failure
    assert result.issues and "no recognizable price field" in result.issues[0]
    client.close()


def test_http_404_not_retried_and_captured(fast_settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is False
    assert result.http_status == 404
    assert calls["n"] == 1  # no retry on a 4xx
    client.close()


def test_http_500_retried_up_to_max_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    settings = Settings(max_retries=2, retry_backoff_seconds=0.01)
    client = PsygridClient(settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is False
    assert result.http_status == 500
    assert calls["n"] == 3  # initial attempt + 2 retries
    client.close()


def test_transient_failure_then_success_recovers() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={"ltp": 100})

    settings = Settings(max_retries=3, retry_backoff_seconds=0.01)
    client = PsygridClient(settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is True
    assert calls["n"] == 3
    client.close()


def test_timeout_exhausted_captured_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    settings = Settings(max_retries=1, retry_backoff_seconds=0.01)
    client = PsygridClient(settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is False
    assert "ConnectTimeout" in result.error
    client.close()


def test_network_error_captured_not_raised(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused", request=request)

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    result = client.fetch_endpoint("underlying", underlying="NIFTY")

    assert result.ok is False
    assert "ConnectError" in result.error
    client.close()


def test_unregistered_endpoint_raises(fast_settings: Settings) -> None:
    client = PsygridClient(fast_settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(UnregisteredEndpointError):
        client.fetch_endpoint("not_a_real_endpoint", underlying="NIFTY")
    client.close()


def test_fetch_snapshot_never_raises_when_everything_fails(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    bundle = client.fetch_snapshot("NIFTY")

    assert bundle.all_critical_ok() is False
    assert all(not r.ok for r in bundle.results.values())
    assert set(bundle.results) == {
        "underlying", "options", "depth", "indicators", "futures",
        "market_breadth", "sectors", "global_context", "rbi_news",
        "india_vix", "live",
    }
    client.close()


def test_fetch_snapshot_one_optional_failure_does_not_affect_others(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/indiavix.json":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"ltp": 1, "data": []})

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    bundle = client.fetch_snapshot("NIFTY")

    assert bundle.results["india_vix"].ok is False
    assert bundle.results["underlying"].ok is True
    assert bundle.all_critical_ok() is True
    client.close()


def test_duplicate_fetch_calls_are_independent(fast_settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ltp": 1})

    client = PsygridClient(fast_settings, transport=httpx.MockTransport(handler))
    r1 = client.fetch_endpoint("underlying", underlying="NIFTY")
    r2 = client.fetch_endpoint("underlying", underlying="NIFTY")
    assert r1.requested_at <= r2.requested_at
    assert r1 is not r2
    client.close()


def test_context_manager_closes_client(fast_settings: Settings) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with PsygridClient(fast_settings, transport=transport) as client:
        client.fetch_endpoint("underlying", underlying="NIFTY")
    assert client._client.is_closed is True
