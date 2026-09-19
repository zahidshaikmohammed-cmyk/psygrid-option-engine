from __future__ import annotations

import pytest

from psygrid_option_engine.api.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    EngineState,
    assert_valid_transition,
)


def test_startup_to_data_loading_valid() -> None:
    assert_valid_transition(EngineState.STARTUP, EngineState.DATA_LOADING)


def test_startup_to_trade_ready_invalid() -> None:
    with pytest.raises(ValueError):
        assert_valid_transition(EngineState.STARTUP, EngineState.TRADE_READY)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert TRANSITIONS[state] == frozenset()


def test_every_state_has_an_entry() -> None:
    assert set(TRANSITIONS.keys()) == set(EngineState)


def test_no_trade_reachable_from_every_gate() -> None:
    gates = (
        EngineState.DATA_VALIDATION,
        EngineState.MARKET_ANALYSIS,
        EngineState.AUTHORIZATION,
        EngineState.CONTRACT_SELECTION,
        EngineState.TRADE_ENGINEERING,
        EngineState.RISK_VALIDATION,
    )
    for gate in gates:
        assert EngineState.NO_TRADE in TRANSITIONS[gate]
