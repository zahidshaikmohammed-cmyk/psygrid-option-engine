"""Confluence aggregation across evidence streams (brief section 20).

Counts supportive/neutral/conflicting/unavailable evidence and lists
contradictions explicitly. Deliberately does NOT apply arbitrary weights -
`authorization/tiers.py` consumes the plain counts, not a weighted score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from psygrid_option_engine.domain.evidence import EvidenceItem, EvidenceStance


@dataclass(frozen=True)
class ConfluenceReport:
    items: tuple[EvidenceItem, ...]
    supportive_count: int
    neutral_count: int
    conflicting_count: int
    unavailable_count: int
    contradictions: tuple[str, ...]

    @property
    def total_weighed(self) -> int:
        """Streams that actually had an opinion (supportive or conflicting),
        excluding neutral/unavailable - this is what tier requirements
        measure independent evidence *streams* against, not raw item count."""
        return self.supportive_count + self.conflicting_count


def build_confluence(items: Sequence[EvidenceItem]) -> ConfluenceReport:
    supportive = [i for i in items if i.stance is EvidenceStance.SUPPORTIVE]
    neutral = [i for i in items if i.stance is EvidenceStance.NEUTRAL]
    conflicting = [i for i in items if i.stance is EvidenceStance.CONFLICTING]
    unavailable = [i for i in items if i.stance is EvidenceStance.UNAVAILABLE]

    contradictions = tuple(f"{i.stream}: {i.detail}" for i in conflicting)

    return ConfluenceReport(
        items=tuple(items),
        supportive_count=len(supportive),
        neutral_count=len(neutral),
        conflicting_count=len(conflicting),
        unavailable_count=len(unavailable),
        contradictions=contradictions,
    )
