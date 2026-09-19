"""Global macro context / news interpretation (brief sections 18-19).

Delayed macro series keep their own `source_date` and are surfaced as
context notes only - never treated as live data or turned into a
standalone signal. Missing series stay explicitly unavailable rather than
being substituted with an unrelated dataset (brief section 19).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.snapshot import GlobalContextSeries, NewsItem

_EVENT_RISK_KEYWORDS = ("rate", "policy", "war", "election", "emergency", "sanction", "crisis")


@dataclass(frozen=True)
class ContextAssessment:
    notes: tuple[str, ...]
    event_risk_flag: bool
    stance: EvidenceStance


def analyze_context(
    global_context: Sequence[GlobalContextSeries], news: Sequence[NewsItem], *, as_of: datetime
) -> ContextAssessment:
    if not global_context and not news:
        return ContextAssessment((), False, EvidenceStance.UNAVAILABLE)

    notes: list[str] = []
    for series in global_context:
        if series.value.available and series.value.value is not None:
            age = f" (as of {series.source_date})" if series.source_date else " (as-of date unknown)"
            notes.append(f"{series.name}: {series.value.value:g}{age}")

    event_risk = False
    for item in news:
        notes.append(f"news: {item.headline}")
        if any(k in item.headline.lower() for k in _EVENT_RISK_KEYWORDS):
            event_risk = True

    stance = EvidenceStance.NEUTRAL if notes else EvidenceStance.UNAVAILABLE
    return ContextAssessment(tuple(notes), event_risk, stance)
