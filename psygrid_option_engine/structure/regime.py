"""Market-regime classification from multiple independent observations
(brief section 7): "never use one indicator to determine regime."

`classify_regime` requires evidence from at least two independent streams
(structure, trend-strength/ADX, volatility expansion-compression, and the
nearest key-level reaction) to commit to anything other than UNCERTAIN —
this is the concrete enforcement of that rule, not just a docstring
promise.
"""

from __future__ import annotations

from collections import defaultdict

from psygrid_option_engine.structure.types import MarketRegime, ReactionKind, RegimeAssessment, TrendBias

_PRIORITY = (
    MarketRegime.LIQUIDITY_SWEEP,
    MarketRegime.FAILED_BREAKOUT,
    MarketRegime.BREAKOUT_ATTEMPT,
    MarketRegime.REVERSAL_ATTEMPT,
    MarketRegime.TRENDING_UP,
    MarketRegime.TRENDING_DOWN,
    MarketRegime.EXPANSION,
    MarketRegime.COMPRESSION,
    MarketRegime.PULLBACK,
    MarketRegime.RANGE,
    MarketRegime.UNCERTAIN,
)


def classify_regime(
    *,
    trend_bias: TrendBias,
    bias_evidence: tuple[str, ...],
    adx_last: float | None,
    bb_width_now: float | None,
    bb_width_avg: float | None,
    nearest_key_reaction: ReactionKind | None,
    min_independent_streams: int = 2,
) -> RegimeAssessment:
    votes: dict[MarketRegime, int] = defaultdict(int)
    evidence: list[str] = []
    streams = 0

    if trend_bias is not TrendBias.NEUTRAL:
        streams += 1
        target = MarketRegime.TRENDING_UP if trend_bias is TrendBias.BULLISH else MarketRegime.TRENDING_DOWN
        votes[target] += 1
        evidence.extend(bias_evidence)
    else:
        streams += 1
        votes[MarketRegime.RANGE] += 1
        evidence.extend(bias_evidence or ("no clear directional swing sequence",))

    if adx_last is not None:
        streams += 1
        if adx_last >= 25:
            trending_target = (
                MarketRegime.TRENDING_UP if trend_bias is TrendBias.BULLISH else MarketRegime.TRENDING_DOWN
            )
            if trend_bias is not TrendBias.NEUTRAL:
                votes[trending_target] += 1
            evidence.append(f"ADX {adx_last:.1f} indicates trending conditions")
        else:
            votes[MarketRegime.RANGE] += 1
            evidence.append(f"ADX {adx_last:.1f} indicates non-trending conditions")

    if bb_width_now is not None and bb_width_avg is not None and bb_width_avg > 0:
        streams += 1
        ratio = bb_width_now / bb_width_avg
        if ratio > 1.2:
            votes[MarketRegime.EXPANSION] += 1
            evidence.append(f"volatility expanding (band width {ratio:.2f}x recent average)")
        elif ratio < 0.8:
            votes[MarketRegime.COMPRESSION] += 1
            evidence.append(f"volatility compressing (band width {ratio:.2f}x recent average)")

    if nearest_key_reaction is not None:
        streams += 1
        if nearest_key_reaction is ReactionKind.SWEEP:
            votes[MarketRegime.LIQUIDITY_SWEEP] += 2
            evidence.append("liquidity sweep detected at nearest key level")
        elif nearest_key_reaction is ReactionKind.FAILED_BREAK:
            votes[MarketRegime.FAILED_BREAKOUT] += 2
            evidence.append("failed breakout at nearest key level")
        elif nearest_key_reaction is ReactionKind.ACCEPTANCE:
            votes[MarketRegime.BREAKOUT_ATTEMPT] += 1
            evidence.append("acceptance beyond nearest key level")
        elif nearest_key_reaction is ReactionKind.REJECTION:
            votes[MarketRegime.REVERSAL_ATTEMPT] += 1
            evidence.append("rejection at nearest key level")

    if streams < min_independent_streams or not votes:
        return RegimeAssessment(
            regime=MarketRegime.UNCERTAIN,
            evidence=tuple(evidence) or ("insufficient independent evidence streams",),
            independent_streams=streams,
        )

    best_count = max(votes.values())
    tied = {regime for regime, count in votes.items() if count == best_count}
    winner = next(r for r in _PRIORITY if r in tied)

    return RegimeAssessment(regime=winner, evidence=tuple(evidence), independent_streams=streams)
