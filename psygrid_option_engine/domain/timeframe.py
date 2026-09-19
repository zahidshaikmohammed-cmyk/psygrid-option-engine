"""Timeframe enum and the canonical `Candle` type.

`Candle.is_closed` is the load-bearing field for the no-lookahead rule
(docs/ARCHITECTURE.md section 3): a candle whose period has not fully
elapsed as of the snapshot's information boundary must never be treated as
a closed bar by structure/momentum/etc. logic. `market/aggregation.py` is
the only place that constructs aggregated candles and is responsible for
setting this correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class Timeframe(IntEnum):
    """Ordered so `coarser-than` comparisons are just `>`."""

    M1 = 1
    M5 = 5
    M15 = 15
    H1 = 60
    D1 = 1440
    W1 = 10080

    @property
    def minutes(self) -> int:
        return int(self.value)


INTRADAY_TIMEFRAMES = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1)
CALENDAR_TIMEFRAMES = (Timeframe.D1, Timeframe.W1)


@dataclass(frozen=True)
class Candle:
    """A single OHLCV bar.

    `start`/`end` are UTC and half-open: `[start, end)`. `end` is the
    instant the bar's period elapses, regardless of whether `is_closed` is
    True — `is_closed` records whether that instant is `<=` the
    information boundary the candle was built under, not whether `end` is
    in the past relative to wall-clock "now".
    """

    timeframe: Timeframe
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    is_closed: bool
    source: str  # "raw" (came directly from upstream) or "aggregated:<base>"
