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
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from psygrid_option_engine.api.runtime import CycleResult, EngineRuntime
from psygrid_option_engine.authorization.tiers import TIER_LABELS, Tier
from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.signals.lifecycle import LifecycleTracker
from psygrid_option_engine.signals.schema import Signal, TradeReadySignal

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
    range_source = ms.get("range_source")
    if range_source:
        lines.append(
            f"Expected range: {_fmt(ms.get('expected_range_lower'))} - "
            f"{_fmt(ms.get('expected_range_upper'))} ({range_source})"
        )
    else:
        lines.append("Expected range: unavailable (no ATR or VIX data yet)")
    week_status = ms.get("week_range_status")
    if week_status == "AVAILABLE":
        lines.append(f"Week H/L: {_fmt(ms.get('current_week_high'))} / {_fmt(ms.get('current_week_low'))}")
    else:
        lines.append(f"Week H/L: {week_status or 'UNAVAILABLE'} (no multi-day history yet)")
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


@dataclass
class ActiveTrade:
    """A TRADE_READY signal being monitored tick-to-tick in --live mode for
    structural invalidation or a premium SL/TP hit. This is intentionally
    kept out of `api/decision.py` (which stays a pure, stateless
    MarketSnapshot -> Signal function per brief section 32) - lifecycle
    monitoring is live-loop bookkeeping, not part of the deterministic
    decision core.
    """

    key: str
    underlying: str
    direction: str
    security_id: str
    entry: float
    stop_loss: float
    take_profit: float
    structural_invalidation_level: float
    triggered_at: datetime


def _current_premium(result: CycleResult, security_id: str, *, as_of: datetime, settings: Settings) -> float | None:
    """Looks up a specific contract's current LTP from this tick's already-
    fetched raw bundle - no extra network call, just re-deriving the
    canonical snapshot from data already on hand."""
    if result.bundle is None:
        return None
    snapshot = build_market_snapshot(result.bundle, as_of=as_of, settings=settings)
    if snapshot.options is None:
        return None
    for leg in snapshot.options.legs:
        if leg.security_id == security_id and leg.ltp.available:
            return leg.ltp.value
    return None


def _register_new_active_trades(
    active_trades: dict[str, ActiveTrade], results: dict[str, CycleResult], now: datetime
) -> None:
    for underlying, result in results.items():
        if underlying in active_trades:
            continue  # already monitoring a trade for this underlying to conclusion
        signal = result.signal
        if not isinstance(signal, TradeReadySignal):
            continue
        active_trades[underlying] = ActiveTrade(
            key=_lifecycle_key(underlying, signal),
            underlying=underlying,
            direction=signal.direction,
            security_id=signal.contract.security_id,
            entry=signal.execution.entry,
            stop_loss=signal.execution.stop_loss,
            take_profit=signal.execution.take_profit,
            structural_invalidation_level=signal.execution.underlying_invalidation_level,
            triggered_at=now,
        )
        print(f"[{underlying}] now monitoring active trade: {signal.contract.symbol} "
              f"entry={signal.execution.entry:.2f} sl={signal.execution.stop_loss:.2f} "
              f"tp={signal.execution.take_profit:.2f}")


def _check_active_trades(
    active_trades: dict[str, ActiveTrade],
    results: dict[str, CycleResult],
    tracker: LifecycleTracker,
    *,
    now: datetime,
    settings: Settings,
) -> bool:
    """Checks every currently-monitored trade against this tick's fresh
    data for a structural-invalidation or premium SL/TP hit. Returns True
    if anything resolved (so the caller knows to print a full report)."""
    resolved = False
    for underlying in list(active_trades):
        trade = active_trades[underlying]
        result = results.get(underlying)
        if result is None or result.signal is None:
            continue

        underlying_ltp = result.signal.market_state.get("ltp")
        structural_hit = False
        if underlying_ltp is not None:
            structural_hit = (
                underlying_ltp <= trade.structural_invalidation_level
                if trade.direction == "CALL"
                else underlying_ltp >= trade.structural_invalidation_level
            )

        premium = _current_premium(result, trade.security_id, as_of=now, settings=settings)
        stop_hit = premium is not None and premium <= trade.stop_loss
        target_hit = premium is not None and premium >= trade.take_profit

        if structural_hit or stop_hit:
            reason = "structural invalidation" if structural_hit else "premium stop hit"
            premium_note = f", premium {premium:.2f}" if premium is not None else ""
            print(f"[{underlying}] TRADE INVALIDATED ({reason}){premium_note} - {trade.security_id}")
            tracker.update(key=trade.key, tier=0, as_of=now, invalidated=True)
            del active_trades[underlying]
            resolved = True
        elif target_hit:
            print(f"[{underlying}] TARGET HIT - {trade.security_id} @ premium {premium:.2f}")
            tracker.update(key=trade.key, tier=0, as_of=now, targeted=True)
            del active_trades[underlying]
            resolved = True

    return resolved


def _run_live(
    runtime: EngineRuntime, underlyings: tuple[str, ...], interval: float, *, settings: Settings | None = None
) -> int:
    settings = settings or get_settings()
    tracker = LifecycleTracker()
    active_trades: dict[str, ActiveTrade] = {}
    print(f"Live mode: refreshing every {interval:.0f}s. Signal-only - this process never places orders.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            now = datetime.now(UTC)
            results = {u: runtime.run_cycle(u, now=now) for u in underlyings}

            trade_resolved = _check_active_trades(active_trades, results, tracker, now=now, settings=settings)
            _register_new_active_trades(active_trades, results, now)

            changed = trade_resolved
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
                status = " | ".join(f"{u} ACTIVE" for u in active_trades) or "no change"
                print(f"[{now.astimezone(IST).strftime('%H:%M:%S')} IST] {status}.")

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
            return _run_live(runtime, underlyings, interval, settings=settings)
        return _run_once(runtime, underlyings)


if __name__ == "__main__":
    sys.exit(main())
