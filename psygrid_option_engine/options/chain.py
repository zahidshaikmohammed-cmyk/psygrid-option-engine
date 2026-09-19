"""Option-chain-level evidence (brief section 14).

Honesty note: PCR / max pain / OI concentration / skew computed here are
*static, single-snapshot* metrics. The brief also asks for "how chain
behaviour changes as the underlying approaches, rejects or accepts
important levels" - that needs a *time series* of chain snapshots, which
this single-fetch `MarketSnapshot` does not carry. `structure/liquidity.py`
already gives the closest honestly-derivable equivalent (OI-concentration
liquidity zones + their price-reaction classification against recent
candles); this module stays purely descriptive rather than pretending to
have dynamic chain-evolution evidence it doesn't have. True chain-evolution
analysis is future `replay/`-accumulated-history work.

The single directional read this module *does* make (extreme PCR skew as
a weak support/resistance-imbalance signal) is a widely-used but genuinely
contested desk heuristic - brief section 14 explicitly warns against
"blindly interpreting simplistic OI rules", so it only fires on clearly
extreme readings and is always reported as `NEUTRAL` otherwise, never
treated as more than one weak, secondary piece of evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.snapshot import OptionChainSnapshot, OptionLeg, OptionType

Direction = Literal["CALL", "PUT"]

_PCR_SUPPORTIVE_FOR_CALL = 1.3
_PCR_SUPPORTIVE_FOR_PUT = 0.77  # ~= 1 / 1.3


@dataclass(frozen=True)
class ChainMetrics:
    atm_strike: float | None
    pcr_oi: float | None
    ce_oi_total: float | None
    pe_oi_total: float | None
    max_pain_strike: float | None
    top_ce_oi_strikes: tuple[float, ...]
    top_pe_oi_strikes: tuple[float, ...]
    iv_skew: float | None  # mean PE IV - mean CE IV among available legs
    stance: EvidenceStance
    detail: str


def compute_chain_metrics(
    chain: OptionChainSnapshot | None,
    *,
    underlying_ltp: float | None,
    direction: Direction,
    expiry: date | None = None,
    top_n: int = 3,
) -> ChainMetrics:
    empty = ((), ())
    if chain is None or not chain.legs:
        return ChainMetrics(
            None, None, None, None, None, *empty, None, EvidenceStance.UNAVAILABLE, "no option chain data"
        )

    legs = chain.for_expiry(expiry) if expiry is not None else chain.legs
    if not legs:
        return ChainMetrics(
            None, None, None, None, None, *empty, None, EvidenceStance.UNAVAILABLE, "no legs for expiry"
        )

    strikes = sorted({leg.strike for leg in legs})
    atm_strike = (
        min(strikes, key=lambda s: abs(s - underlying_ltp)) if underlying_ltp is not None else None
    )

    ce_with_oi = _strike_value_pairs(legs, "CE", lambda leg: leg.oi.value)
    pe_with_oi = _strike_value_pairs(legs, "PE", lambda leg: leg.oi.value)

    ce_oi_total = sum(v for _, v in ce_with_oi) if ce_with_oi else None
    pe_oi_total = sum(v for _, v in pe_with_oi) if pe_with_oi else None
    pcr_oi = None
    if ce_oi_total not in (None, 0) and pe_oi_total is not None:
        pcr_oi = pe_oi_total / ce_oi_total

    top_ce = tuple(s for s, _ in sorted(ce_with_oi, key=lambda t: t[1], reverse=True)[:top_n])
    top_pe = tuple(s for s, _ in sorted(pe_with_oi, key=lambda t: t[1], reverse=True)[:top_n])

    max_pain_strike = _max_pain(strikes, ce_with_oi, pe_with_oi)

    ce_ivs = [v for _, v in _strike_value_pairs(legs, "CE", lambda leg: leg.iv.value)]
    pe_ivs = [v for _, v in _strike_value_pairs(legs, "PE", lambda leg: leg.iv.value)]
    iv_skew = (sum(pe_ivs) / len(pe_ivs) - sum(ce_ivs) / len(ce_ivs)) if ce_ivs and pe_ivs else None

    stance = EvidenceStance.NEUTRAL
    detail_parts = []
    if pcr_oi is not None:
        detail_parts.append(f"PCR(OI) {pcr_oi:.2f}")
        if pcr_oi >= _PCR_SUPPORTIVE_FOR_CALL:
            stance = EvidenceStance.SUPPORTIVE if direction == "CALL" else EvidenceStance.CONFLICTING
        elif pcr_oi <= _PCR_SUPPORTIVE_FOR_PUT:
            stance = EvidenceStance.SUPPORTIVE if direction == "PUT" else EvidenceStance.CONFLICTING
    else:
        stance = EvidenceStance.UNAVAILABLE
        detail_parts.append("PCR unavailable (OI data missing)")

    if max_pain_strike is not None:
        detail_parts.append(f"max pain {max_pain_strike:g}")

    return ChainMetrics(
        atm_strike=atm_strike,
        pcr_oi=pcr_oi,
        ce_oi_total=ce_oi_total,
        pe_oi_total=pe_oi_total,
        max_pain_strike=max_pain_strike,
        top_ce_oi_strikes=top_ce,
        top_pe_oi_strikes=top_pe,
        iv_skew=iv_skew,
        stance=stance,
        detail="; ".join(detail_parts),
    )


def _strike_value_pairs(
    legs: Sequence[OptionLeg], option_type: OptionType, extract: Callable[[OptionLeg], float | None]
) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for leg in legs:
        if leg.option_type != option_type:
            continue
        value = extract(leg)
        if value is not None:
            pairs.append((leg.strike, value))
    return pairs


def _max_pain(
    strikes: list[float], ce_with_oi: list[tuple[float, float]], pe_with_oi: list[tuple[float, float]]
) -> float | None:
    if not ce_with_oi and not pe_with_oi:
        return None
    best_strike: float | None = None
    best_pain: float | None = None
    for k in strikes:
        ce_pain = sum(max(k - s, 0) * oi for s, oi in ce_with_oi)
        pe_pain = sum(max(s - k, 0) * oi for s, oi in pe_with_oi)
        pain = ce_pain + pe_pain
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, k
    return best_strike
