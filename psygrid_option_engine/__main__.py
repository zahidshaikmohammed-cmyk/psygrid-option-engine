"""CLI entrypoint: `python -m psygrid_option_engine`.

Runs one decision cycle and prints a human-readable report. See
docs/PHASES.md — Phases 3-9 are not implemented, so this currently reports
data-loading/validation results, not a trading signal, unless critical data
is unavailable (in which case a real NO_TRADE signal, with reasons, is
produced and printed).
"""

from __future__ import annotations

import argparse
import json
import sys

from psygrid_option_engine.api.runtime import EngineRuntime
from psygrid_option_engine.config.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="psygrid-option-engine")
    parser.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY"])
    args = parser.parse_args(argv)

    settings = get_settings()
    with EngineRuntime(settings) as runtime:
        result = runtime.run_cycle(args.underlying)

    print(f"state: {result.state.value}")
    print(f"underlying: {result.underlying}")
    print(f"message: {result.message}")
    if result.data_quality is not None:
        print(f"data_quality.overall: {result.data_quality.overall}")
        print(f"data_quality.critical_endpoints_ok: {result.data_quality.critical_endpoints_ok}")
        if result.data_quality.stale_fields:
            print(f"data_quality.stale_fields: {result.data_quality.stale_fields}")
        if result.data_quality.unavailable_fields:
            print(f"data_quality.unavailable_fields: {result.data_quality.unavailable_fields}")
    if result.signal is not None:
        print("signal:")
        print(json.dumps(result.signal.model_dump(mode="json"), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
