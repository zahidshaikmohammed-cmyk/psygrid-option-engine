#!/usr/bin/env python3
"""Probe the real PSYGRID upstream and capture sample payloads.

This repository was built in a sandbox with no network route to the
production host, so `docs/ENDPOINTS.md` and `psygrid_option_engine/data/
models.py` are best-guess. Run this script somewhere with network access to
the real upstream, then commit the captured JSON under
`tests/fixtures/upstream_samples/` so the contract docs and models can be
corrected against ground truth (see docs/PHASES.md, "Next steps").

Usage:
    python scripts/probe_upstream.py --base-url http://140.245.226.102:10000 --out ./upstream_samples

This performs read-only GET requests only. It does not write to or
authenticate against the upstream service.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from psygrid_option_engine.data.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointScope,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. http://140.245.226.102:10000")
    parser.add_argument("--out", default="./upstream_samples", help="output directory")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}
    for endpoint in ENDPOINT_REGISTRY.values():
        if endpoint.scope is EndpointScope.GLOBAL:
            paths[endpoint.logical_name] = endpoint.path()
        else:
            for underlying, slug_name in (("nifty", "NIFTY"), ("banknifty", "BANKNIFTY")):
                paths[f"{endpoint.logical_name}_{underlying}"] = endpoint.path(slug_name)

    ok, failed = 0, 0
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        for name, path in sorted(paths.items()):
            dest = out_dir / f"{name}.json"
            try:
                response = client.get(path)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:  # noqa: BLE001 - this is a diagnostic script
                print(f"FAIL  {path:40s} -> {type(exc).__name__}: {exc}", file=sys.stderr)
                failed += 1
                continue
            dest.write_text(json.dumps(data, indent=2, sort_keys=True))
            print(f"OK    {path:40s} -> {dest}")
            ok += 1

    print(f"\n{ok} succeeded, {failed} failed. Samples written to {out_dir}/")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
