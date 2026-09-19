"""Shared evidence-stance vocabulary (brief section 20: every evidence
stream reports SUPPORTIVE / NEUTRAL / CONFLICTING / UNAVAILABLE).

Used by every `market/*` and `options/*` evidence-producing module, and
consumed by `authorization/confluence.py`. Keeping this in `domain/`
(rather than duplicated per-module or owned by `authorization/`) is what
lets `market/` and `options/` stay independent of `authorization/` —
they produce evidence, they don't know how it will be weighed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvidenceStance(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class EvidenceItem:
    stream: str
    stance: EvidenceStance
    detail: str
