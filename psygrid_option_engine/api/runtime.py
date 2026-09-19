"""Orchestration entrypoint. Drives the full state machine
(STARTUP..TRADE_READY/NO_TRADE/SESSION_COMPLETE) for one decision cycle.
See docs/STATE_MACHINE.md and docs/PHASES.md.

The actual authorization/selection/execution/risk pipeline is
`api/decision.py::decide` - the same function `replay/engine.py` uses -
this module is only responsible for the network fetch, the session-window
short-circuit, and mapping the outcome onto the documented state graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psygrid_option_engine.api.decision import decide
from psygrid_option_engine.api.state_machine import EngineState, assert_valid_transition
from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.client import PsygridClient
from psygrid_option_engine.data.exceptions import ConfigurationError
from psygrid_option_engine.data.models import RawFetchBundle
from psygrid_option_engine.data.snapshot_builder import build_market_snapshot
from psygrid_option_engine.signals.schema import DataQuality, Signal


@dataclass(frozen=True)
class CycleResult:
    state: EngineState
    underlying: str
    bundle: RawFetchBundle | None
    data_quality: DataQuality | None
    signal: Signal | None
    message: str


class EngineRuntime:
    """Owns a `PsygridClient` and runs one decision cycle at a time.

    `run_cycle` never raises for upstream data problems (those become a
    `NO_TRADE` signal or an early terminal state) — only for programmer/
    configuration errors (unsupported underlying).
    """

    def __init__(self, settings: Settings | None = None, *, client: PsygridClient | None = None):
        self._settings = settings or get_settings()
        self._client = client or PsygridClient(self._settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EngineRuntime:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def run_cycle(self, underlying: str, *, now: datetime | None = None) -> CycleResult:
        state = EngineState.STARTUP

        if underlying not in self._settings.supported_underlyings:
            raise ConfigurationError(
                f"Unsupported underlying {underlying!r}; supported: "
                f"{self._settings.supported_underlyings}"
            )

        now = now or datetime.now(UTC)
        session_window = self._settings.session_window()

        assert_valid_transition(state, EngineState.DATA_LOADING)
        state = EngineState.DATA_LOADING

        if not session_window.is_within_session(now):
            # We still "enter" DATA_VALIDATION conceptually (the session
            # check is a validation concern per docs/STATE_MACHINE.md) but
            # skip the network round trip entirely, since no fetched data
            # could change this outcome.
            assert_valid_transition(state, EngineState.DATA_VALIDATION)
            state = EngineState.DATA_VALIDATION
            assert_valid_transition(state, EngineState.SESSION_COMPLETE)
            return CycleResult(
                state=EngineState.SESSION_COMPLETE,
                underlying=underlying,
                bundle=None,
                data_quality=None,
                signal=None,
                message=f"Outside trading session window at {now.isoformat()}.",
            )

        bundle = self._client.fetch_snapshot(underlying)

        assert_valid_transition(state, EngineState.DATA_VALIDATION)
        state = EngineState.DATA_VALIDATION

        snapshot = build_market_snapshot(bundle, as_of=now, settings=self._settings)
        data_quality = snapshot.data_quality

        if not data_quality.critical_endpoints_ok:
            assert_valid_transition(state, EngineState.NO_TRADE)
            signal = decide(snapshot, settings=self._settings)
            return CycleResult(
                state=EngineState.NO_TRADE,
                underlying=underlying,
                bundle=bundle,
                data_quality=data_quality,
                signal=signal,
                message="Critical upstream data unavailable or structurally invalid.",
            )

        assert_valid_transition(state, EngineState.MARKET_ANALYSIS)
        state = EngineState.MARKET_ANALYSIS
        assert_valid_transition(state, EngineState.STRUCTURE_ANALYSIS)
        state = EngineState.STRUCTURE_ANALYSIS
        assert_valid_transition(state, EngineState.AUTHORIZATION)
        state = EngineState.AUTHORIZATION

        # `decide()` performs authorization -> contract selection -> trade
        # engineering -> risk validation as one deterministic function
        # (shared with replay/engine.py); we still walk the documented
        # transition graph here so it stays an enforced, checked contract.
        signal = decide(snapshot, settings=self._settings)

        if signal.state == "TRADE_READY":
            for frm, to in (
                (EngineState.AUTHORIZATION, EngineState.CONTRACT_SELECTION),
                (EngineState.CONTRACT_SELECTION, EngineState.TRADE_ENGINEERING),
                (EngineState.TRADE_ENGINEERING, EngineState.RISK_VALIDATION),
                (EngineState.RISK_VALIDATION, EngineState.TRADE_READY),
            ):
                assert_valid_transition(frm, to)
            final_state = EngineState.TRADE_READY
            message = "Trade-ready signal produced."
        else:
            assert_valid_transition(state, EngineState.NO_TRADE)
            final_state = EngineState.NO_TRADE
            message = "No actionable trade this cycle."

        return CycleResult(
            state=final_state,
            underlying=underlying,
            bundle=bundle,
            data_quality=data_quality,
            signal=signal,
            message=message,
        )
