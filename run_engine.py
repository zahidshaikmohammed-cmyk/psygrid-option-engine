#!/usr/bin/env python3
"""PSYGRID Option Engine - CLI entrypoint (brief section 35).

    python run_engine.py --once [--underlying NIFTY|BANKNIFTY|BOTH]
    python run_engine.py --live [--interval SECONDS]

Signal-only. This process NEVER places a broker order - see docs/SAFETY.md.
`--once` runs a single complete intelligence cycle against production data
and prints a human-readable report plus the current best opportunity
across both underlyings (or just the one requested). `--live` repeats
that cycle on an interval, printing only when something actually changed
(via `signals/lifecycle.py`) so it doesn't spam an unchanged setup.
"""

from __future__ import annotations

import argparse
import sys
import time as time_module
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from psygrid_option_engine.api.runtime import CycleResult, EngineRuntime
from psygrid_option_engine.authorization.tiers import TIER_LABELS, Tier
from psygrid_option_engine.config.settings import get_settings
from psygrid_option_engine.signals.lifecycle import LifecycleTracker
from psygrid_option_engine.signals.schema import Signal

IST = ZoneInfo("Asia/Kolkata")
UNDERLYINGS = ("NIFTY", "BANKNIFTY")
_BAR = "=" * 60


def _fmt(value: float | None, *, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _tier_label(signal: Signal) -> str:
    try:
        return TIER_LABELS[Tier(signal.tier)]
    except ValueError:
        return "UNKNOWN"


def _format_header(now: datetime) -> str:
    ist_now = now.astimezone(IST)
    return f"{_BAR}\n{'PSYGRID OPTION ENGINE':^60}\n{ist_now.strftime('%H:%M:%S IST'):^60}\n{_BAR}"


def _underlying_block(underlying: str, result: CycleResult) -> str:
    lines = [f"--- {underlying} ---"]
    signal = result.signal
    if signal is None:
        lines.append(result.message)
        return "\n".join(lines)

    ms = signal.market_state
    st = signal.structure
    lines.append(f"LTP: {_fmt(ms.get('ltp'))}  (day chg {_fmt(ms.get('price_change_pct'))}%)")
    lines.append(f"VWAP: {_fmt(ms.get('vwap'))} ({ms.get('vwap_relation') or 'n/a'})")
    lines.append(f"Session H/L: {_fmt(ms.get('session_high'))} / {_fmt(ms.get('session_low'))}")
    lines.append(f"Regime: {st.get('regime', 'UNKNOWN')} (trend bias: {st.get('trend_bias', 'UNKNOWN')})")
    lines.append(f"Data quality: {ms.get('data_quality', 'UNKNOWN')}")
    lines.append(f"Tier: {signal.tier} - {_tier_label(signal)}  [{signal.state}]")
    lines.append("Evidence:")
    for reason in signal.reasons[:8]:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def _best_across(results: dict[str, CycleResult]) -> tuple[str, CycleResult] | None:
    candidates = [(u, r) for u, r in results.items() if r.signal is not None]
    if not candidates:
        return None

    def key(item: tuple[str, CycleResult]) -> tuple[int, int]:
        signal = item[1].signal
        assert signal is not None
        return (1 if signal.state == "TRADE_READY" else 0, signal.tier)

    return max(candidates, key=key)


def _format_best_opportunity(best: tuple[str, CycleResult]) -> str:
    underlying, result = best
    signal = result.signal
    assert signal is not None
    lines = [_BAR, f"{'CURRENT BEST OPPORTUNITY':^60}", _BAR, ""]

    if signal.state == "TRADE_READY":
        lines.append(f"TIER: {signal.tier} - {_tier_label(signal)}")
        lines.append("")
        contract = signal.contract
        lines.append(f"{underlying} {contract.strike:g} {contract.option_type}")
        lines.append(f"Expiry: {contract.expiry}")
        lines.append(f"Security ID: {contract.security_id}")
        lines.append("")
        execution = signal.execution
        lines.append(f"ENTRY: {execution.entry:.2f}")
        lines.append(f"SL: {execution.stop_loss:.2f}")
        lines.append(f"TP: {execution.take_profit:.2f}")
        lines.append(f"R:R: {execution.risk_reward:.2f}")
        lines.append("")
        lines.append("STRUCTURAL INVALIDATION:")
        lines.append(f"{underlying} {execution.structural_invalidation}")
        lines.append("")
        lines.append("WHY:")
        for reason in signal.reasons:
            lines.append(f"  - {reason}")
        lines.append("")
        lines.append(f"DATA QUALITY: {signal.data_quality.overall}")
    else:
        lines.append("NO ACTIONABLE TRADE")
        lines.append("")
        lines.append(f"CURRENT STATE ({underlying}):")
        for reason in signal.reasons[:5]:
            lines.append(f"  - {reason}")
        setup = signal.best_developing_setup
        if setup is not None:
            lines.append("")
            lines.append(f"BEST DEVELOPING SETUP: {underlying} {setup.direction} via {setup.framework}")
            lines.append(f"  Tier: {setup.tier} - {setup.tier_label}")
            if setup.missing_confirmation:
                lines.append("  MISSING CONFIRMATION:")
                for item in setup.missing_confirmation:
                    lines.append(f"    - {item}")
            lines.append(f"  UPGRADE CONDITION: {setup.upgrade_condition}")
            lines.append(f"  INVALIDATION: {setup.invalidation_condition}")

    lines.append(_BAR)
    return "\n".join(lines)


def _run_once(runtime: EngineRuntime, underlyings: tuple[str, ...], *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    results = {u: runtime.run_cycle(u, now=now) for u in underlyings}

    print(_format_header(now))
    print()
    for u in underlyings:
        print(_underlying_block(u, results[u]))
        print()

    best = _best_across(results)
    if best is not None:
        print(_format_best_opportunity(best))
    else:
        print("No underlying produced a decision this cycle (see messages above).")
    return 0


def _lifecycle_key(underlying: str, signal: Signal) -> str:
    if signal.state == "TRADE_READY":
        framework = signal.authorization.get("framework", "?")
        return f"{underlying}:{signal.direction}:{framework}"
    setup = signal.best_developing_setup
    if setup is not None:
        return f"{underlying}:{setup.direction}:{setup.framework}"
    return f"{underlying}:NONE"


def _run_live(runtime: EngineRuntime, underlyings: tuple[str, ...], interval: float) -> int:
    tracker = LifecycleTracker()
    print(f"Live mode: refreshing every {interval:.0f}s. Signal-only - this process never places orders.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            now = datetime.now(UTC)
            results = {u: runtime.run_cycle(u, now=now) for u in underlyings}

            changed = False
            for u, result in results.items():
                if result.signal is None:
                    continue
                key = _lifecycle_key(u, result.signal)
                tracker.update(key=key, tier=result.signal.tier, as_of=now)
                if not tracker.is_repeat(key, result.signal.tier):
                    changed = True

            if changed:
                print(_format_header(now))
                print()
                for u in underlyings:
                    print(_underlying_block(u, results[u]))
                    print()
                best = _best_across(results)
                if best is not None:
                    print(_format_best_opportunity(best))
            else:
                print(f"[{now.astimezone(IST).strftime('%H:%M:%S')} IST] no change.")

            time_module.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_engine.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one complete cycle and exit (default).")
    mode.add_argument("--live", action="store_true", help="Continuously refresh and report.")
    parser.add_argument("--underlying", choices=["NIFTY", "BANKNIFTY", "BOTH"], default="BOTH")
    parser.add_argument(
        "--interval", type=float, default=None, help="Seconds between cycles in --live mode."
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    underlyings = UNDERLYINGS if args.underlying == "BOTH" else (args.underlying,)
    interval = args.interval or settings.decision_interval_seconds

    with EngineRuntime(settings) as runtime:
        if args.live:
            return _run_live(runtime, underlyings, interval)
        return _run_once(runtime, underlyings)


if __name__ == "__main__":
    sys.exit(main())
