"""Tests for run_engine.py - loaded by path since it's a top-level CLI
script, not part of the installed package (mirrors tests/test_probe_upstream.py)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from psygrid_option_engine.api.runtime import CycleResult
from psygrid_option_engine.api.state_machine import EngineState
from psygrid_option_engine.config.settings import EndpointCriticality, Settings
from psygrid_option_engine.data.models import EndpointFetchResult, RawFetchBundle
from psygrid_option_engine.signals.lifecycle import LifecycleState, LifecycleTracker
from psygrid_option_engine.signals.schema import (
    ContractRef,
    DataQuality,
    DevelopingSetup,
    ExecutionPlan,
    ExecutionQuality,
    NoTradeSignal,
    TradeReadySignal,
)

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "run_engine.py"
_spec = importlib.util.spec_from_file_location("run_engine", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
run_engine = importlib.util.module_from_spec(_spec)
sys.modules["run_engine"] = run_engine
_spec.loader.exec_module(run_engine)

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _data_quality(overall: str = "GOOD") -> DataQuality:
    return DataQuality(overall=overall, critical_endpoints_ok=(overall != "INSUFFICIENT"))


def _no_trade_signal(*, tier: int = 0, setup: DevelopingSetup | None = None) -> NoTradeSignal:
    return NoTradeSignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=_data_quality(),
        tier=tier,
        market_state={"ltp": 24000.0, "price_change_pct": 1.0, "vwap": 23950.0, "vwap_relation": "ABOVE"},
        structure={"regime": "RANGE", "trend_bias": "NEUTRAL"},
        reasons=["no strategy framework is currently applicable"],
        best_developing_setup=setup,
    )


def _trade_ready_signal(*, tier: int = 2) -> TradeReadySignal:
    return TradeReadySignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=_data_quality(),
        tier=tier,
        market_state={"ltp": 24555.0, "price_change_pct": 2.25, "vwap": 24280.0, "vwap_relation": "ABOVE"},
        structure={"regime": "TRENDING_UP", "trend_bias": "BULLISH"},
        authorization={"framework": "TREND_CONTINUATION"},
        reasons=["structure bullish", "momentum strong"],
        direction="CALL",
        contract=ContractRef(
            security_id="CE1", symbol="NIFTY24550CE", underlying="NIFTY",
            expiry=date(2026, 9, 25), strike=24550, option_type="CE",
        ),
        execution=ExecutionPlan(
            entry=120.0, stop_loss=90.0, take_profit=170.0, risk_reward=1.67,
            structural_invalidation="underlying CALL thesis invalidates at 24478",
            underlying_invalidation_level=24478.0,
        ),
        execution_quality=ExecutionQuality(spread_pct=0.8, depth_assessment="GOOD", liquidity_assessment="GOOD"),
    )


def test_tier_label_known() -> None:
    sig = _no_trade_signal(tier=2)
    assert run_engine._tier_label(sig) == "VALID / MODERATE-CONFIDENCE"


def test_underlying_block_no_trade() -> None:
    result = CycleResult(
        state=EngineState.NO_TRADE, underlying="NIFTY", bundle=None,
        data_quality=_data_quality(), signal=_no_trade_signal(), message="x",
    )
    block = run_engine._underlying_block("NIFTY", result)
    assert "NIFTY" in block
    assert "24,000.00" in block
    assert "[NO_TRADE]" in block


def test_underlying_block_missing_signal_uses_message() -> None:
    result = CycleResult(
        state=EngineState.SESSION_COMPLETE, underlying="NIFTY", bundle=None,
        data_quality=None, signal=None, message="Outside trading session window.",
    )
    block = run_engine._underlying_block("NIFTY", result)
    assert "Outside trading session window." in block


def test_best_across_prefers_trade_ready_over_no_trade() -> None:
    results = {
        "NIFTY": CycleResult(EngineState.NO_TRADE, "NIFTY", None, _data_quality(), _no_trade_signal(tier=3), "x"),
        "BANKNIFTY": CycleResult(
            EngineState.TRADE_READY, "BANKNIFTY", None, _data_quality(), _trade_ready_signal(tier=1), "x"
        ),
    }
    best = run_engine._best_across(results)
    assert best is not None
    assert best[0] == "BANKNIFTY"


def test_best_across_prefers_higher_tier_among_same_state() -> None:
    results = {
        "NIFTY": CycleResult(EngineState.NO_TRADE, "NIFTY", None, _data_quality(), _no_trade_signal(tier=1), "x"),
        "BANKNIFTY": CycleResult(
            EngineState.NO_TRADE, "BANKNIFTY", None, _data_quality(), _no_trade_signal(tier=3), "x"
        ),
    }
    best = run_engine._best_across(results)
    assert best is not None
    assert best[0] == "BANKNIFTY"


def test_best_across_empty_when_no_signals() -> None:
    results = {
        "NIFTY": CycleResult(EngineState.SESSION_COMPLETE, "NIFTY", None, None, None, "x"),
    }
    assert run_engine._best_across(results) is None


def test_format_best_opportunity_trade_ready_contains_key_fields() -> None:
    result = CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x")
    text = run_engine._format_best_opportunity(("NIFTY", result))
    assert "TIER: 2" in text
    assert "NIFTY 24550 CE" in text
    assert "ENTRY: 120.00" in text
    assert "SL: 90.00" in text
    assert "TP: 170.00" in text
    assert "R:R: 1.67" in text


def test_format_best_opportunity_no_trade_reports_developing_setup() -> None:
    setup = DevelopingSetup(
        framework="TREND_CONTINUATION", direction="CALL", tier=0, tier_label="MARKET/SETUP ONLY",
        evidence_summary=["structure bullish"], missing_confirmation=["risk/reward >= 1.2"],
        upgrade_condition="risk/reward >= 1.2", invalidation_condition="underlying trades through 24478",
    )
    result = CycleResult(
        EngineState.NO_TRADE, "NIFTY", None, _data_quality(), _no_trade_signal(setup=setup), "x"
    )
    text = run_engine._format_best_opportunity(("NIFTY", result))
    assert "NO ACTIONABLE TRADE" in text
    assert "BEST DEVELOPING SETUP: NIFTY CALL via TREND_CONTINUATION" in text
    assert "risk/reward >= 1.2" in text
    assert "UPGRADE CONDITION" in text
    assert "INVALIDATION" in text


class _StubRuntime:
    def __init__(self, results: dict[str, CycleResult]) -> None:
        self._results = results

    def run_cycle(self, underlying: str, *, now: object = None) -> CycleResult:
        return self._results[underlying]


def test_run_once_prints_report(capsys: pytest.CaptureFixture) -> None:
    results = {
        "NIFTY": CycleResult(EngineState.NO_TRADE, "NIFTY", None, _data_quality(), _no_trade_signal(), "x"),
    }
    exit_code = run_engine._run_once(_StubRuntime(results), ("NIFTY",), now=NOW)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "PSYGRID OPTION ENGINE" in out
    assert "NIFTY" in out


def test_run_live_stops_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    results = {
        "NIFTY": CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x"),
    }

    call_count = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(run_engine.time_module, "sleep", fake_sleep)
    exit_code = run_engine._run_live(_StubRuntime(results), ("NIFTY",), 0.01)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Stopped." in out
    assert call_count["n"] == 2


# --- active-trade (invalidation/target) monitoring -------------------------


def _options_bundle(security_id: str, ltp: float, *, as_of: datetime) -> RawFetchBundle:
    result = EndpointFetchResult(
        logical_name="options", url="/public/nifty-options.json", criticality=EndpointCriticality.CRITICAL,
        requested_at=as_of, fetched_at=as_of, latency_ms=1.0, http_status=200,
        data={
            "expiry": "2026-09-25",
            "strikes": [{"strike": 24550, "ce": {"security_id": security_id, "last_price": ltp}}],
        },
        observed_at=as_of, issues=(), error=None,
    )
    return RawFetchBundle(underlying="NIFTY", requested_at=as_of, results={"options": result})


def _trade(**overrides: object) -> run_engine.ActiveTrade:  # noqa: F821 - resolved at runtime via loaded module
    defaults: dict = dict(
        key=run_engine._lifecycle_key("NIFTY", _trade_ready_signal()),
        underlying="NIFTY", direction="CALL", security_id="CE1",
        entry=120.0, stop_loss=90.0, take_profit=170.0,
        structural_invalidation_level=24478.0, triggered_at=NOW,
    )
    defaults.update(overrides)
    return run_engine.ActiveTrade(**defaults)


def _result_with_ltp_and_premium(*, ltp: float, security_id: str | None, premium: float | None) -> CycleResult:
    signal = _no_trade_signal()
    signal = signal.model_copy(update={"market_state": {**signal.market_state, "ltp": ltp}})
    bundle = _options_bundle(security_id, premium, as_of=NOW) if (security_id and premium is not None) else None
    return CycleResult(EngineState.NO_TRADE, "NIFTY", bundle, _data_quality(), signal, "x")


def test_register_new_active_trades_adds_entry() -> None:
    results = {
        "NIFTY": CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x")
    }
    active: dict = {}
    run_engine._register_new_active_trades(active, results, NOW)
    assert "NIFTY" in active
    assert active["NIFTY"].security_id == "CE1"
    assert active["NIFTY"].direction == "CALL"


def test_register_skips_if_already_tracked() -> None:
    results = {
        "NIFTY": CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x")
    }
    existing = _trade(security_id="OLD")
    active = {"NIFTY": existing}
    run_engine._register_new_active_trades(active, results, NOW)
    assert active["NIFTY"] is existing


def test_register_skips_no_trade_signal() -> None:
    results = {"NIFTY": CycleResult(EngineState.NO_TRADE, "NIFTY", None, _data_quality(), _no_trade_signal(), "x")}
    active: dict = {}
    run_engine._register_new_active_trades(active, results, NOW)
    assert active == {}


def test_current_premium_found_in_bundle() -> None:
    bundle = _options_bundle("CE1", 150.0, as_of=NOW)
    result = CycleResult(EngineState.TRADE_READY, "NIFTY", bundle, _data_quality(), _trade_ready_signal(), "x")
    assert run_engine._current_premium(result, "CE1", as_of=NOW, settings=Settings()) == 150.0


def test_current_premium_missing_bundle_returns_none() -> None:
    result = CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x")
    assert run_engine._current_premium(result, "CE1", as_of=NOW, settings=Settings()) is None


def test_current_premium_security_not_found_returns_none() -> None:
    bundle = _options_bundle("OTHER", 150.0, as_of=NOW)
    result = CycleResult(EngineState.TRADE_READY, "NIFTY", bundle, _data_quality(), _trade_ready_signal(), "x")
    assert run_engine._current_premium(result, "CE1", as_of=NOW, settings=Settings()) is None


def test_check_active_trades_structural_invalidation_call() -> None:
    active = {"NIFTY": _trade(direction="CALL", structural_invalidation_level=24478.0)}
    results = {"NIFTY": _result_with_ltp_and_premium(ltp=24400.0, security_id=None, premium=None)}
    tracker = LifecycleTracker()
    resolved = run_engine._check_active_trades(active, results, tracker, now=NOW, settings=Settings())
    assert resolved is True
    assert "NIFTY" not in active
    tracked = tracker.get(_trade().key)
    assert tracked is not None
    assert tracked.state is LifecycleState.INVALIDATED


def test_check_active_trades_premium_stop_hit() -> None:
    active = {"NIFTY": _trade(stop_loss=90.0)}
    results = {"NIFTY": _result_with_ltp_and_premium(ltp=24600.0, security_id="CE1", premium=85.0)}
    tracker = LifecycleTracker()
    resolved = run_engine._check_active_trades(active, results, tracker, now=NOW, settings=Settings())
    assert resolved is True
    assert "NIFTY" not in active


def test_check_active_trades_target_hit() -> None:
    active = {"NIFTY": _trade(take_profit=170.0)}
    results = {"NIFTY": _result_with_ltp_and_premium(ltp=24700.0, security_id="CE1", premium=180.0)}
    tracker = LifecycleTracker()
    resolved = run_engine._check_active_trades(active, results, tracker, now=NOW, settings=Settings())
    assert resolved is True
    assert "NIFTY" not in active
    tracked = tracker.get(_trade().key)
    assert tracked is not None
    assert tracked.state is LifecycleState.TARGET


def test_check_active_trades_no_hit_keeps_monitoring() -> None:
    active = {"NIFTY": _trade()}
    results = {"NIFTY": _result_with_ltp_and_premium(ltp=24600.0, security_id="CE1", premium=125.0)}
    tracker = LifecycleTracker()
    resolved = run_engine._check_active_trades(active, results, tracker, now=NOW, settings=Settings())
    assert resolved is False
    assert "NIFTY" in active


class _SequenceStubRuntime:
    def __init__(self, sequence: list[CycleResult]) -> None:
        self._sequence = sequence
        self._i = 0

    def run_cycle(self, underlying: str, *, now: object = None) -> CycleResult:
        result = self._sequence[min(self._i, len(self._sequence) - 1)]
        self._i += 1
        return result


def test_run_live_end_to_end_registers_then_reports_target_hit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tick1 = CycleResult(EngineState.TRADE_READY, "NIFTY", None, _data_quality(), _trade_ready_signal(), "x")
    tick2 = _result_with_ltp_and_premium(ltp=24700.0, security_id="CE1", premium=180.0)

    runtime = _SequenceStubRuntime([tick1, tick2])
    call_count = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(run_engine.time_module, "sleep", fake_sleep)
    exit_code = run_engine._run_live(runtime, ("NIFTY",), 0.01, settings=Settings())
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "now monitoring active trade" in out
    assert "TARGET HIT" in out
