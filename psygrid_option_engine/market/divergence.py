"""Divergence detection across evidence streams (brief section 17).

Divergence is evidence, not an automatic reversal signal - `authorization/`
decides what to do with it; this module only detects and labels it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.market.breadth import BreadthEvidence
from psygrid_option_engine.market.futures_analysis import FuturesEvidence
from psygrid_option_engine.market.momentum import ImpulseMetrics

Direction = Literal["CALL", "PUT"]
Severity = Literal["LOW", "MEDIUM", "HIGH"]


class DivergenceKind(StrEnum):
    BREADTH_CONFLICT = "BREADTH_CONFLICT"  # index direction not confirmed by breadth
    VOLUME_WEAK = "VOLUME_WEAK"  # price move not confirmed by volume expansion
    MOMENTUM_DECAYING = "MOMENTUM_DECAYING"  # price move not confirmed by momentum quality
    FUTURES_CONFLICT = "FUTURES_CONFLICT"  # futures OI/price read conflicts with the thesis
    SECTOR_CONFLICT = "SECTOR_CONFLICT"  # sector participation conflicts with the thesis


@dataclass(frozen=True)
class Divergence:
    kind: DivergenceKind
    description: str
    severity: Severity


def detect_divergences(
    *,
    price_change_pct: float | None,
    breadth: BreadthEvidence | None,
    momentum: ImpulseMetrics | None,
    futures: FuturesEvidence | None,
    direction: Direction,
) -> list[Divergence]:
    divergences: list[Divergence] = []

    if breadth is not None and breadth.stance is EvidenceStance.CONFLICTING:
        divergences.append(
            Divergence(
                DivergenceKind.BREADTH_CONFLICT,
                f"underlying direction ({direction}) not confirmed by market breadth",
                "MEDIUM",
            )
        )

    if momentum is not None and price_change_pct is not None and abs(price_change_pct) > 0:
        if momentum.volume_expansion_ratio is not None and momentum.volume_expansion_ratio < 0.8:
            divergences.append(
                Divergence(
                    DivergenceKind.VOLUME_WEAK,
                    "price movement not confirmed by volume expansion",
                    "LOW",
                )
            )
        if momentum.acceleration is not None and momentum.size != 0:
            decelerating = (momentum.acceleration > 0) != (momentum.size > 0)
            if decelerating:
                divergences.append(
                    Divergence(
                        DivergenceKind.MOMENTUM_DECAYING,
                        "momentum decelerating within the move",
                        "LOW",
                    )
                )

    if futures is not None and futures.stance is EvidenceStance.CONFLICTING:
        relationship = futures.price_oi_relationship.value
        divergences.append(
            Divergence(
                DivergenceKind.FUTURES_CONFLICT,
                f"futures price/OI relationship ({relationship}) conflicts with {direction}",
                "MEDIUM",
            )
        )

    return divergences
