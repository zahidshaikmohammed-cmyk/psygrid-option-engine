from __future__ import annotations

from datetime import UTC, datetime

import pytest

import psygrid_option_engine.__main__ as cli
from psygrid_option_engine.api.runtime import CycleResult
from psygrid_option_engine.api.state_machine import EngineState
from psygrid_option_engine.signals.schema import DataQuality, NoTradeSignal


class _StubRuntime:
    def __init__(self, result: CycleResult) -> None:
        self._result = result

    def __enter__(self) -> _StubRuntime:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def run_cycle(self, underlying: str, **_kwargs: object) -> CycleResult:
        return self._result


def test_cli_prints_session_complete(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    result = CycleResult(
        state=EngineState.SESSION_COMPLETE,
        underlying="NIFTY",
        bundle=None,
        data_quality=None,
        signal=None,
        message="Outside trading session window.",
    )
    monkeypatch.setattr(cli, "EngineRuntime", lambda settings: _StubRuntime(result))

    exit_code = cli.main(["--underlying", "NIFTY"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "state: SESSION_COMPLETE" in out
    assert "Outside trading session window." in out


def test_cli_prints_no_trade_signal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    dq = DataQuality(overall="INSUFFICIENT", critical_endpoints_ok=False, unavailable_fields=["options"])
    signal = NoTradeSignal(
        decision_timestamp=datetime.now(UTC),
        underlying="NIFTY",
        data_quality=dq,
        reasons=["critical endpoint 'options' failed: HTTP 503"],
    )
    result = CycleResult(
        state=EngineState.NO_TRADE,
        underlying="NIFTY",
        bundle=None,
        data_quality=dq,
        signal=signal,
        message="Critical upstream data unavailable.",
    )
    monkeypatch.setattr(cli, "EngineRuntime", lambda settings: _StubRuntime(result))

    exit_code = cli.main(["--underlying", "NIFTY"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "state: NO_TRADE" in out
    assert '"state": "NO_TRADE"' in out
    assert "critical endpoint 'options' failed" in out


def test_cli_rejects_unsupported_underlying() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--underlying", "SENSEX"])
