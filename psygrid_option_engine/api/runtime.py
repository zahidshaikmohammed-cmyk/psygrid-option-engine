"""Orchestration entrypoint. Drives the state machine through the phases
implemented so far (STARTUP..DATA_VALIDATION) and reports honestly where it
stops. See docs/STATE_MACHINE.md and docs/PHASES.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psygrid_option_engine.api.state_machine import EngineState, assert_valid_transition
from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.data.client import PsygridClient
from psygrid_option_engine.data.exceptions import ConfigurationError
from psygrid_option_engine.data.models import RawFetchBundle
from psygrid_option_engine.data.validation import build_data_quality
from psygrid_option_engine.signals.schema import DataQuality, NoTradeSignal, Signal


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

        data_quality = build_data_quality(bundle, settings=self._settings, as_of=now)

        if not data_quality.critical_endpoints_ok:
            reasons = _critical_failure_reasons(bundle)
            assert_valid_transition(state, EngineState.NO_TRADE)
            signal = NoTradeSignal(
                decision_timestamp=now,
                underlying=underlying,  # type: ignore[arg-type]
                data_quality=data_quality,
                reasons=reasons,
            )
            return CycleResult(
                state=EngineState.NO_TRADE,
                underlying=underlying,
                bundle=bundle,
                data_quality=data_quality,
                signal=signal,
                message="Critical upstream data unavailable or structurally invalid.",
            )

        assert_valid_transition(state, EngineState.MARKET_ANALYSIS)
        return CycleResult(
            state=EngineState.MARKET_ANALYSIS,
            underlying=underlying,
            bundle=bundle,
            data_quality=data_quality,
            signal=None,
            message=(
                "Critical data OK. Structure/authorization/contract-selection/"
                "execution/risk pipeline (Phases 3-9) is not yet implemented; "
                "no TRADE_READY/NO_TRADE signal produced this cycle."
            ),
        )


def _critical_failure_reasons(bundle: RawFetchBundle) -> list[str]:
    reasons: list[str] = []
    for name, result in bundle.critical_results().items():
        if result.error is not None:
            reasons.append(f"critical endpoint '{name}' failed: {result.error}")
        elif result.data is None:
            reasons.append(f"critical endpoint '{name}' returned no data")
        elif result.issues:
            reasons.append(f"critical endpoint '{name}' structurally invalid: {'; '.join(result.issues)}")
    if not reasons:
        reasons.append("critical endpoint data unavailable for an unspecified reason")
    return reasons
