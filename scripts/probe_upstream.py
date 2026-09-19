#!/usr/bin/env python3
"""Probe the real PSYGRID upstream and capture sample payloads.

This repository was built in a sandbox with no network route to the
production host, so `docs/ENDPOINTS.md` and `psygrid_option_engine/data/
models.py` are best-guess. Run this script somewhere with network access to
the real upstream (see the "Oracle probe procedure" in docs/ENDPOINTS.md),
then commit the resulting artifact so the contract docs and models can be
corrected against ground truth (see docs/PHASES.md, "Next steps").

This performs read-only GET requests only. It never writes to, deploys to,
or authenticates against the upstream service, and it never modifies any
server-side state.

Output: a single JSON artifact (default `artifacts/production_endpoint_samples.json`)
containing, per endpoint: the request path, HTTP status, response latency,
a safe allowlisted subset of response headers, the response body's own
self-reported timestamp (if any), computed freshness, structural-validation
issues (per psygrid_option_engine.data.validation), and the JSON body
itself with secret-shaped fields/values redacted (see `_redact`).

Usage:
    python scripts/probe_upstream.py --base-url http://140.245.226.102:10000

    # add endpoints not in the registry (e.g. live-*.json variants, or
    # stock/{symbol}.json once you know representative symbols):
    python scripts/probe_upstream.py --base-url http://140.245.226.102:10000 \\
        --extra /public/live-nifty.json \\
        --extra /public/stock/RELIANCE.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from psygrid_option_engine.data.endpoints import ENDPOINT_REGISTRY, EndpointScope
from psygrid_option_engine.data.validation import extract_timestamp, validate_structure

USER_AGENT = "psygrid-option-engine-probe/1.0 (+read-only contract verification)"

# --- Secret redaction -------------------------------------------------
#
# These are documented public endpoints and should not carry credentials,
# but per the operator's instructions this script redacts defensively
# rather than trusting that assumption. Two independent passes:
#   1. Key-name based: any dict key that looks like it names a secret has
#      its value replaced, regardless of the value's shape.
#   2. Value-shape based: any string that *looks* like an opaque token
#      (long, no spaces, token-charset, not a timestamp) is replaced even
#      under an innocuous-looking key. Over-redacting is the safe failure
#      mode here, not under-redacting.

REDACTED = "***REDACTED***"

_SECRET_KEY_PATTERN = re.compile(
    r"(key|secret|token|password|passwd|credential|auth|bearer|dhan|fred[_-]?api)",
    re.IGNORECASE,
)

_TOKEN_SHAPE_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.]{32,}$")

_SAFE_RESPONSE_HEADERS = {
    "date",
    "content-type",
    "content-length",
    "server",
    "last-modified",
    "etag",
    "cache-control",
    "age",
}


def _looks_like_timestamp(s: str) -> bool:
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _looks_like_secret_value(s: str) -> bool:
    if s.lower().startswith(("bearer ", "sk-", "xox")):
        return True
    if " " in s or len(s) < 32:
        return False
    if _looks_like_timestamp(s):
        return False
    return bool(_TOKEN_SHAPE_PATTERN.match(s))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_KEY_PATTERN.search(str(k)):
                out[k] = REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and _looks_like_secret_value(value):
        return REDACTED
    return value


def _redact_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() in _SAFE_RESPONSE_HEADERS}


# --- Endpoint list ------------------------------------------------------


def _registry_paths() -> dict[str, str]:
    """Every endpoint currently registered in data/endpoints.py, for both
    underlyings where applicable. This already covers every endpoint named
    in docs/ENDPOINTS.md plus `live.json` (registered as GLOBAL/OPTIONAL)."""
    paths: dict[str, str] = {}
    for endpoint in ENDPOINT_REGISTRY.values():
        if endpoint.scope is EndpointScope.GLOBAL:
            paths[endpoint.logical_name] = endpoint.path()
        else:
            for slug, underlying in (("nifty", "NIFTY"), ("banknifty", "BANKNIFTY")):
                paths[f"{endpoint.logical_name}_{slug}"] = endpoint.path(underlying)
    return paths


def _fetch_one(client: httpx.Client, path: str, retries: int) -> dict[str, Any]:
    requested_at = datetime.now(UTC)
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        start = time.monotonic()
        try:
            response = client.get(path)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0)
                continue
            return {
                "path": path,
                "http_status": None,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "requested_at": requested_at.isoformat(),
                "fetched_at": None,
                "latency_ms": None,
            }
        latency_ms = (time.monotonic() - start) * 1000
        fetched_at = datetime.now(UTC)

        result: dict[str, Any] = {
            "path": path,
            "http_status": response.status_code,
            "requested_at": requested_at.isoformat(),
            "fetched_at": fetched_at.isoformat(),
            "latency_ms": round(latency_ms, 2),
            "response_headers": _redact_headers(response.headers),
        }

        if response.status_code >= 400:
            result["ok"] = False
            result["error"] = f"HTTP {response.status_code}"
            return result

        try:
            body = response.json()
        except ValueError as exc:
            result["ok"] = False
            result["error"] = f"invalid JSON: {exc}"
            return result

        raw_bytes = response.content
        observed_at = extract_timestamp(body)
        freshness_seconds = (
            (fetched_at - observed_at).total_seconds() if observed_at is not None else None
        )

        result["ok"] = True
        result["error"] = None
        result["body_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        result["observed_at"] = observed_at.isoformat() if observed_at else None
        result["freshness_seconds"] = freshness_seconds
        result["structural_issues"] = list(validate_structure(_logical_guess(path), body))
        result["body"] = _redact(body)
        return result

    # Unreachable, but keeps mypy happy about a guaranteed return.
    raise last_exc if last_exc else RuntimeError("unreachable")


def _logical_guess(path: str) -> str:
    """Best-effort mapping from a URL path back to a logical_name so
    validate_structure can apply the right (best-guess) shape check."""
    name = path.rsplit("/", 1)[-1].removesuffix(".json")
    for logical in ("options", "depth", "indicators", "futures"):
        if name.endswith(f"-{logical}"):
            return logical
    if name in ("nifty", "banknifty"):
        return "underlying"
    return name.replace("-", "_")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", required=True, help="e.g. http://140.245.226.102:10000")
    parser.add_argument(
        "--out",
        default="artifacts/production_endpoint_samples.json",
        help="output artifact path (default: artifacts/production_endpoint_samples.json)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=1, help="retries per endpoint on network error")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="/public/....json",
        help="additional absolute path(s) to probe beyond the registered set; repeatable",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = _registry_paths()
    for i, extra in enumerate(args.extra):
        paths[f"extra_{i}_{extra.strip('/').replace('/', '_')}"] = extra

    probed_at = datetime.now(UTC)
    endpoints: dict[str, Any] = {}
    ok_count, fail_count = 0, 0

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(base_url=args.base_url, timeout=args.timeout, headers=headers) as client:
        for name, path in sorted(paths.items()):
            result = _fetch_one(client, path, args.retries)
            endpoints[name] = result
            if result["ok"]:
                ok_count += 1
                issues = f" (issues: {result['structural_issues']})" if result["structural_issues"] else ""
                print(f"OK    {path:40s} status={result['http_status']} {issues}")
            else:
                fail_count += 1
                print(f"FAIL  {path:40s} -> {result['error']}", file=sys.stderr)

    artifact = {
        "probe_meta": {
            "base_url": args.base_url,
            "probed_at": probed_at.isoformat(),
            "endpoint_count": len(paths),
            "ok_count": ok_count,
            "fail_count": fail_count,
            "redaction": "key-name and token-shape based; see _redact() in this script",
        },
        "endpoints": endpoints,
    }

    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))

    print(f"\n{ok_count} succeeded, {fail_count} failed.")
    print(f"Artifact written to {out_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
