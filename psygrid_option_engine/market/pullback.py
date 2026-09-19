"""Pullback classification (brief section 11).

Design note: depth (shallow/moderate/deep/failed), continuation-vs-reversal,
and one-leg-vs-two-leg structure are three independent axes of evidence.
Cramming them into a single enum would lose information, so `kind` covers
depth/failure/reversal/continuation and `legs` is reported separately —
callers that specifically want "one-leg vs two-leg" read `legs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PullbackType(StrEnum):
    NONE = "NONE"
    SHALLOW = "SHALLOW"
    MODERATE = "MODERATE"
    DEEP = "DEEP"
    FAILED = "FAILED"  # retraced beyond 100% of the impulse without reversing structure
    CONTINUATION = "CONTINUATION"  # price resumed beyond the impulse's own extreme after the pullback
    REVERSAL_STRUCTURE = "REVERSAL_STRUCTURE"  # price traded beyond the impulse's own origin


@dataclass(frozen=True)
class PullbackAssessment:
    kind: PullbackType
    retracement_pct: float | None
    legs: int
    evidence: tuple[str, ...]


def classify_pullback(
    *,
    impulse_start: float,
    impulse_end: float,
    current_price: float,
    post_impulse_swing_count: int = 0,
    made_new_extreme_beyond_start: bool = False,
    resumed_beyond_impulse_end: bool = False,
) -> PullbackAssessment:
    span = impulse_end - impulse_start
    if span == 0:
        no_range = ("no impulse range to measure retracement against",)
        return PullbackAssessment(PullbackType.NONE, None, post_impulse_swing_count, no_range)

    retracement_pct = abs((impulse_end - current_price) / span) * 100
    evidence = [f"retraced {retracement_pct:.1f}% of the preceding impulse"]
    evidence.append(f"{post_impulse_swing_count} counter-trend leg(s) observed within the pullback")

    if made_new_extreme_beyond_start:
        evidence.append("price traded beyond the impulse's own origin - a structure reversal, not a pullback")
        return PullbackAssessment(
            PullbackType.REVERSAL_STRUCTURE, retracement_pct, post_impulse_swing_count, tuple(evidence)
        )

    if resumed_beyond_impulse_end:
        evidence.append("price resumed beyond the impulse's extreme after the pullback - continuation")
        return PullbackAssessment(
            PullbackType.CONTINUATION, retracement_pct, post_impulse_swing_count, tuple(evidence)
        )

    if retracement_pct > 100:
        evidence.append("retracement exceeded the full impulse without reversing structure")
        kind = PullbackType.FAILED
    elif retracement_pct <= 38.2:
        kind = PullbackType.SHALLOW
    elif retracement_pct <= 61.8:
        kind = PullbackType.MODERATE
    else:
        kind = PullbackType.DEEP

    return PullbackAssessment(kind, retracement_pct, post_impulse_swing_count, tuple(evidence))
