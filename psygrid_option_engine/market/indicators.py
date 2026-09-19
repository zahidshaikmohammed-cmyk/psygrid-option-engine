"""Pure, deterministic indicator math over OHLCV series.

Every function here is a pure function of its inputs — no network calls,
no wall-clock reads, no hidden state (docs/ARCHITECTURE.md section 6 / the
brief's section 34). All series-returning functions return a list the same
length as their input, front-padded with `None` for the warm-up period —
callers take the last non-None value for "the current reading" and can
still inspect the trailing window for slope/direction.

These are evidence for `structure/` and `authorization/` to weigh, never a
standalone strategy (per the brief's repeated "EMA9 > EMA20 -> CALL is not
acceptable" rule) — nothing in this module makes a trading decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean, pstdev
from typing import Literal

from psygrid_option_engine.domain.timeframe import Candle

Direction = Literal["UP", "DOWN"]


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def sma(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = fmean(values[i - period + 1 : i + 1])
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = fmean(values[:period])
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def true_range(candles: Sequence[Candle]) -> list[float]:
    out: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
        else:
            prev_close = candles[i - 1].close
            out.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return out


def _wilder_smooth(values: Sequence[float], period: int) -> list[float | None]:
    """Wilder's smoothing: seed with a simple average of the first `period`
    values, then `smoothed[i] = (smoothed[i-1] * (period-1) + values[i]) / period`.
    Used by ATR, ADX's DM/TR smoothing, and RSI's average gain/loss."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = fmean(values[:period])
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    return _wilder_smooth(true_range(candles), period)


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. `out[i]` is available once `i >= period` (needs
    `period` price changes, i.e. `period + 1` closes)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]

    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_averages(avg_gain, avg_loss)
    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd_line, signal_line, histogram), each the length of `closes`."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow, strict=True)
    ]

    first_valid = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * len(closes)
    histogram: list[float | None] = [None] * len(closes)
    if first_valid is not None:
        valid_macd = [v for v in macd_line[first_valid:] if v is not None]
        signal_valid = ema(valid_macd, signal)
        for offset, value in enumerate(signal_valid):
            signal_line[first_valid + offset] = value
        for i in range(len(closes)):
            if macd_line[i] is not None and signal_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]  # type: ignore[operator]
    return macd_line, signal_line, histogram


def adx(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    """Wilder's ADX. Needs roughly `2 * period` bars of warm-up before the
    first value is available."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

    tr = true_range(candles)[1:]  # align with the DM series (both start at index 1)
    smoothed_tr = _wilder_smooth(tr, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    dx: list[float | None] = [None] * len(tr)
    for i in range(len(tr)):
        stv, spdm, smdm = smoothed_tr[i], smoothed_plus_dm[i], smoothed_minus_dm[i]
        if stv is None or spdm is None or smdm is None or stv == 0:
            continue
        plus_di = 100 * spdm / stv
        minus_di = 100 * smdm / stv
        denom = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denom if denom != 0 else 0.0

    dx_values = [v for v in dx if v is not None]
    first_dx_idx = next((i for i, v in enumerate(dx) if v is not None), None)
    if first_dx_idx is None or len(dx_values) < period:
        return out

    adx_smoothed = _wilder_smooth(dx_values, period)
    for offset, value in enumerate(adx_smoothed):
        if value is not None:
            # dx[i] corresponds to candles[i+1] (DM/TR series is offset by 1)
            out[first_dx_idx + offset + 1] = value
    return out


def rvol(volumes: Sequence[float | None], period: int = 20) -> list[float | None]:
    """Current volume relative to the average of the preceding `period`
    bars (current bar excluded from its own baseline)."""
    out: list[float | None] = [None] * len(volumes)
    for i in range(period, len(volumes)):
        window = volumes[i - period : i]
        current = volumes[i]
        if current is None or any(v is None for v in window):
            continue
        baseline = fmean(window)  # type: ignore[arg-type]
        if baseline > 0:
            out[i] = current / baseline
    return out


def bollinger_bands(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (upper, middle, lower). Middle is the SMA; bands use the
    population standard deviation of the same window (Bollinger's original
    definition)."""
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        std = pstdev(window)
        m = mid[i]
        if m is not None:
            upper[i] = m + num_std * std
            lower[i] = m - num_std * std
    return upper, mid, lower


def donchian_channel(
    candles: Sequence[Candle], period: int = 20
) -> tuple[list[float | None], list[float | None]]:
    """Returns (upper, lower): the highest high / lowest low over the
    trailing `period` bars, inclusive of the current bar."""
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    upper: list[float | None] = [None] * len(candles)
    lower: list[float | None] = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        upper[i] = max(highs[i - period + 1 : i + 1])
        lower[i] = min(lows[i - period + 1 : i + 1])
    return upper, lower


def supertrend(
    candles: Sequence[Candle], period: int = 10, multiplier: float = 3.0
) -> tuple[list[float | None], list[Direction | None]]:
    """Classic iterative Supertrend. Returns (value, direction)."""
    n = len(candles)
    value: list[float | None] = [None] * n
    direction: list[Direction | None] = [None] * n
    atr_values = atr(candles, period)

    final_upper: float | None = None
    final_lower: float | None = None

    for i in range(n):
        a = atr_values[i]
        if a is None:
            continue
        hl2 = (candles[i].high + candles[i].low) / 2
        basic_upper = hl2 + multiplier * a
        basic_lower = hl2 - multiplier * a

        if final_upper is None or final_lower is None:
            final_upper, final_lower = basic_upper, basic_lower
            value[i] = final_lower
            direction[i] = "UP"
            continue

        prev_close = candles[i - 1].close
        final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
        final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower

        prev_value = value[i - 1]
        close = candles[i].close
        if prev_value == final_upper:
            if close <= final_upper:
                value[i], direction[i] = final_upper, "DOWN"
            else:
                value[i], direction[i] = final_lower, "UP"
        else:
            if close >= final_lower:
                value[i], direction[i] = final_lower, "UP"
            else:
                value[i], direction[i] = final_upper, "DOWN"
    return value, direction


def vwap(candles: Sequence[Candle]) -> list[float | None]:
    """Cumulative volume-weighted average price over the supplied series.
    Callers MUST pass a session-scoped series (e.g. today's bars from
    session open) — this function has no notion of session boundaries and
    will happily compute a meaningless multi-day VWAP over arbitrary input."""
    out: list[float | None] = [None] * len(candles)
    cum_pv = 0.0
    cum_vol = 0.0
    for i, c in enumerate(candles):
        typical = (c.high + c.low + c.close) / 3
        vol = c.volume or 0.0
        cum_pv += typical * vol
        cum_vol += vol
        out[i] = (cum_pv / cum_vol) if cum_vol > 0 else typical
    return out
