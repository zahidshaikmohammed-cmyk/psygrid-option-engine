"""Engine state machine. See docs/STATE_MACHINE.md for the full narrative
and rationale; this module is the enum + transition graph it describes.

Only STARTUP -> DATA_LOADING -> DATA_VALIDATION -> {SESSION_COMPLETE,
NO_TRADE, MARKET_ANALYSIS} is currently driven by real logic
(`api/runtime.py`). States from MARKET_ANALYSIS onward are represented here
so the graph is documented end-to-end and `assert_valid_transition` can be
used by Phase 3+ code without redefining the graph, but nothing in this
repository currently produces those transitions.
"""

from __future__ import annotations

from enum import StrEnum


class EngineState(StrEnum):
    STARTUP = "STARTUP"
    DATA_LOADING = "DATA_LOADING"
    DATA_VALIDATION = "DATA_VALIDATION"
    WARMING_UP = "WARMING_UP"
    MARKET_ANALYSIS = "MARKET_ANALYSIS"
    STRUCTURE_ANALYSIS = "STRUCTURE_ANALYSIS"
    AUTHORIZATION = "AUTHORIZATION"
    CONTRACT_SELECTION = "CONTRACT_SELECTION"
    TRADE_ENGINEERING = "TRADE_ENGINEERING"
    RISK_VALIDATION = "RISK_VALIDATION"
    TRADE_READY = "TRADE_READY"
    NO_TRADE = "NO_TRADE"
    INVALIDATED = "INVALIDATED"
    SESSION_COMPLETE = "SESSION_COMPLETE"
    ERROR = "ERROR"


TERMINAL_STATES = frozenset(
    {
        EngineState.TRADE_READY,
        EngineState.NO_TRADE,
        EngineState.SESSION_COMPLETE,
        EngineState.ERROR,
    }
)

# Documented transition graph from docs/STATE_MACHINE.md.
TRANSITIONS: dict[EngineState, frozenset[EngineState]] = {
    EngineState.STARTUP: frozenset({EngineState.DATA_LOADING, EngineState.ERROR}),
    EngineState.DATA_LOADING: frozenset({EngineState.DATA_VALIDATION, EngineState.ERROR}),
    EngineState.DATA_VALIDATION: frozenset(
        {EngineState.SESSION_COMPLETE, EngineState.NO_TRADE, EngineState.WARMING_UP,
         EngineState.MARKET_ANALYSIS}
    ),
    EngineState.WARMING_UP: frozenset({EngineState.MARKET_ANALYSIS, EngineState.SESSION_COMPLETE}),
    EngineState.MARKET_ANALYSIS: frozenset({EngineState.STRUCTURE_ANALYSIS, EngineState.NO_TRADE}),
    EngineState.STRUCTURE_ANALYSIS: frozenset({EngineState.AUTHORIZATION}),
    EngineState.AUTHORIZATION: frozenset({EngineState.CONTRACT_SELECTION, EngineState.NO_TRADE}),
    EngineState.CONTRACT_SELECTION: frozenset({EngineState.TRADE_ENGINEERING, EngineState.NO_TRADE}),
    EngineState.TRADE_ENGINEERING: frozenset({EngineState.RISK_VALIDATION, EngineState.NO_TRADE}),
    EngineState.RISK_VALIDATION: frozenset({EngineState.TRADE_READY, EngineState.NO_TRADE}),
    EngineState.TRADE_READY: frozenset(),
    EngineState.NO_TRADE: frozenset(),
    EngineState.INVALIDATED: frozenset(),
    EngineState.SESSION_COMPLETE: frozenset(),
    EngineState.ERROR: frozenset(),
}


def assert_valid_transition(frm: EngineState, to: EngineState) -> None:
    allowed = TRANSITIONS.get(frm, frozenset())
    if to not in allowed:
        raise ValueError(
            f"Invalid state transition {frm.value} -> {to.value}; "
            f"allowed from {frm.value}: {sorted(s.value for s in allowed)}"
        )
