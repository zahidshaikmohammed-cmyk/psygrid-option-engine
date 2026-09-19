"""Futures basis/OI evidence (brief section 13).

The classic price/OI four-quadrant read (long buildup / short buildup /
long unwinding / short covering) is a widely-used heuristic, not a proven
rule - brief section 13 explicitly warns against "blindly interpreting
simplistic OI rules". It is surfaced here as one labeled, documented piece
of evidence for `authorization/` to weigh alongside everything else, never
as a standalone signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.snapshot import FuturesSnapshot

Direction = Literal["CALL", "PUT"]


class OiStance(StrEnum):
    LONG_BUILDUP = "LONG_BUILDUP"
    SHORT_BUILDUP = "SHORT_BUILDUP"
    LONG_UNWINDING = "LONG_UNWINDING"
    SHORT_COVERING = "SHORT_COVERING"
    UNCLEAR = "UNCLEAR"


@dataclass(frozen=True)
class FuturesEvidence:
    basis: float | None
    basis_pct: float | None
    oi_change_pct: float | None
    price_oi_relationship: OiStance
    stance: EvidenceStance
    detail: str


def analyze_futures(
    futures: FuturesSnapshot | None,
    *,
    underlying_ltp: float | None,
    price_change_pct: float | None,
    direction: Direction,
) -> FuturesEvidence:
    if futures is None or futures.nearest is None or underlying_ltp is None:
        return FuturesEvidence(
            None, None, None, OiStance.UNCLEAR, EvidenceStance.UNAVAILABLE, "futures data unavailable"
        )

    leg = futures.nearest
    fut_ltp = leg.ltp.value
    basis = (fut_ltp - underlying_ltp) if fut_ltp is not None else None
    basis_pct = (basis / underlying_ltp * 100) if basis is not None else None

    oi = leg.oi.value
    oi_change = leg.oi_change.value
    oi_change_pct = (oi_change / oi * 100) if oi_change is not None and oi else None

    relationship = OiStance.UNCLEAR
    if price_change_pct is not None and oi_change is not None:
        if price_change_pct > 0 and oi_change > 0:
            relationship = OiStance.LONG_BUILDUP
        elif price_change_pct < 0 and oi_change > 0:
            relationship = OiStance.SHORT_BUILDUP
        elif price_change_pct < 0 and oi_change < 0:
            relationship = OiStance.LONG_UNWINDING
        elif price_change_pct > 0 and oi_change < 0:
            relationship = OiStance.SHORT_COVERING

    bullish_relationship = relationship in (OiStance.LONG_BUILDUP, OiStance.SHORT_COVERING)
    bearish_relationship = relationship in (OiStance.SHORT_BUILDUP, OiStance.LONG_UNWINDING)

    if relationship is OiStance.UNCLEAR:
        stance = EvidenceStance.NEUTRAL
    elif (direction == "CALL" and bullish_relationship) or (direction == "PUT" and bearish_relationship):
        stance = EvidenceStance.SUPPORTIVE
    elif (direction == "CALL" and bearish_relationship) or (direction == "PUT" and bullish_relationship):
        stance = EvidenceStance.CONFLICTING
    else:
        stance = EvidenceStance.NEUTRAL

    detail_parts = []
    if basis_pct is not None:
        detail_parts.append(f"basis {basis_pct:+.2f}%")
    detail_parts.append(f"price/OI read: {relationship.value}")
    detail = "; ".join(detail_parts)

    return FuturesEvidence(basis, basis_pct, oi_change_pct, relationship, stance, detail)
