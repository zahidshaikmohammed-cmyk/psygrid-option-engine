"""PSYGRID upstream HTTP client (section 5 of the brief).

Responsibilities: HTTP retrieval, timeout handling, retry handling, response
validation, schema validation, timestamp normalization, source metadata
preservation, graceful upstream failure. `fetch_endpoint`/`fetch_snapshot`
never raise for endpoint-level failures (timeout, connection error, HTTP
error, malformed JSON) — those are captured into
`EndpointFetchResult.error` so one bad optional endpoint can never crash a
snapshot fetch. Only a programmer error (an unregistered endpoint name)
raises.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.endpoints import (
    ENDPOINT_REGISTRY,
    global_endpoints,
    per_underlying_endpoints,
)
from psygrid_option_engine.data.exceptions import UnregisteredEndpointError
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.data.validation import extract_timestamp, validate_structure


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return False


class PsygridClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        timeout = httpx.Timeout(
            timeout=self._settings.http_timeout_seconds,
            connect=self._settings.http_connect_timeout_seconds,
        )
        self._client = httpx.Client(
            base_url=self._settings.base_url, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PsygridClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _get(self, path: str) -> httpx.Response:
        response = self._client.get(path)
        response.raise_for_status()
        return response

    def fetch_endpoint(
        self, logical_name: str, *, underlying: str | None = None
    ) -> EndpointFetchResult:
        endpoint = ENDPOINT_REGISTRY.get(logical_name)
        if endpoint is None:
            raise UnregisteredEndpointError(logical_name)

        path = endpoint.path(underlying)
        requested_at = datetime.now(UTC)
        start = time.monotonic()

        retrying = retry(
            reraise=True,
            stop=stop_after_attempt(self._settings.max_retries + 1),
            wait=wait_exponential_jitter(initial=self._settings.retry_backoff_seconds, max=10),
            retry=retry_if_exception(_is_retryable),
        )

        def _fail(status: int | None, error: str) -> EndpointFetchResult:
            return EndpointFetchResult(
                logical_name=logical_name,
                url=path,
                criticality=endpoint.criticality,
                requested_at=requested_at,
                fetched_at=datetime.now(UTC),
                latency_ms=(time.monotonic() - start) * 1000,
                http_status=status,
                data=None,
                observed_at=None,
                issues=(),
                error=error,
            )

        try:
            response = retrying(self._get)(path)
        except httpx.HTTPStatusError as exc:
            return _fail(exc.response.status_code, f"HTTP {exc.response.status_code}")
        except httpx.HTTPError as exc:
            return _fail(None, f"{type(exc).__name__}: {exc}")

        fetched_at = datetime.now(UTC)
        latency_ms = (time.monotonic() - start) * 1000

        try:
            data = response.json()
        except ValueError as exc:
            return EndpointFetchResult(
                logical_name=logical_name,
                url=path,
                criticality=endpoint.criticality,
                requested_at=requested_at,
                fetched_at=fetched_at,
                latency_ms=latency_ms,
                http_status=response.status_code,
                data=None,
                observed_at=None,
                issues=(),
                error=f"invalid JSON: {exc}",
            )

        return EndpointFetchResult(
            logical_name=logical_name,
            url=path,
            criticality=endpoint.criticality,
            requested_at=requested_at,
            fetched_at=fetched_at,
            latency_ms=latency_ms,
            http_status=response.status_code,
            data=data,
            observed_at=extract_timestamp(data),
            issues=validate_structure(logical_name, data),
            error=None,
        )

    def fetch_snapshot(self, underlying: str) -> RawFetchBundle:
        """Fetch every registered endpoint relevant to `underlying`
        (per-underlying + global endpoints), tolerating individual
        failures. Never raises for endpoint-level failures."""
        requested_at = datetime.now(UTC)
        results: dict[str, EndpointFetchResult] = {}

        for endpoint in per_underlying_endpoints():
            results[endpoint.logical_name] = self.fetch_endpoint(
                endpoint.logical_name, underlying=underlying
            )
        for endpoint in global_endpoints():
            results[endpoint.logical_name] = self.fetch_endpoint(endpoint.logical_name)

        return RawFetchBundle(underlying=underlying, requested_at=requested_at, results=results)
