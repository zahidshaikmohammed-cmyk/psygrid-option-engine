"""Breadth / sector participation evidence (brief section 16).

Answers "is this move broadly supported or narrowly supported?" - and,
for BANKNIFTY specifically, whether banking sector names are actually
confirming the move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.snapshot import BreadthSnapshot, SectorSnapshot

Direction = Literal["CALL", "PUT"]

_SUPPORTIVE_RATIO_CALL = 1.5
_CONFLICTING_RATIO_CALL = 0.67


@dataclass(frozen=True)
class BreadthEvidence:
    advance_decline_ratio: float | None
    supportive_sector_pct: float | None
    stance: EvidenceStance
    detail: str


def analyze_breadth(
    breadth: BreadthSnapshot | None,
    sectors: Sequence[SectorSnapshot],
    *,
    direction: Direction,
    underlying: str,
) -> BreadthEvidence:
    if breadth is None or not breadth.advances.available or not breadth.declines.available:
        return BreadthEvidence(None, None, EvidenceStance.UNAVAILABLE, "breadth data unavailable")

    adv, dec = breadth.advances.value, breadth.declines.value
    if adv is None or dec is None:
        return BreadthEvidence(None, None, EvidenceStance.UNAVAILABLE, "breadth data unavailable")

    ratio: float | None
    if dec > 0:
        ratio = adv / dec
    elif adv > 0:
        ratio = float("inf")
    else:
        ratio = None

    relevant: list[tuple[SectorSnapshot, float]] = [
        (s, s.change_pct.value) for s in sectors if s.change_pct.available and s.change_pct.value is not None
    ]
    supportive_pct: float | None = None
    if relevant:
        aligned = sum(1 for _, value in relevant if (value > 0) == (direction == "CALL"))
        supportive_pct = 100 * aligned / len(relevant)

    if ratio is None:
        stance = EvidenceStance.UNAVAILABLE
    elif direction == "CALL":
        if ratio > _SUPPORTIVE_RATIO_CALL:
            stance = EvidenceStance.SUPPORTIVE
        elif ratio < _CONFLICTING_RATIO_CALL:
            stance = EvidenceStance.CONFLICTING
        else:
            stance = EvidenceStance.NEUTRAL
    else:
        if ratio < _CONFLICTING_RATIO_CALL:
            stance = EvidenceStance.SUPPORTIVE
        elif ratio > _SUPPORTIVE_RATIO_CALL:
            stance = EvidenceStance.CONFLICTING
        else:
            stance = EvidenceStance.NEUTRAL

    has_finite_ratio = ratio is not None and ratio != float("inf")
    ratio_note = f"A/D ratio {ratio:.2f}" if has_finite_ratio else "A/D ratio unavailable"
    detail_parts = [ratio_note]
    if supportive_pct is not None:
        detail_parts.append(f"{supportive_pct:.0f}% of sectors aligned")

    if underlying == "BANKNIFTY":
        banking = [(s, value) for s, value in relevant if "bank" in s.name.lower()]
        if banking:
            bank_aligned = all((value > 0) == (direction == "CALL") for _, value in banking)
            detail_parts.append("banking sectors " + ("confirming" if bank_aligned else "not confirming"))

    return BreadthEvidence(ratio, supportive_pct, stance, "; ".join(detail_parts))
