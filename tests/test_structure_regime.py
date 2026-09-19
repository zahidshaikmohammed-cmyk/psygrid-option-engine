from __future__ import annotations

from psygrid_option_engine.structure.regime import classify_regime
from psygrid_option_engine.structure.types import MarketRegime, ReactionKind, TrendBias


def test_single_stream_is_uncertain() -> None:
    result = classify_regime(
        trend_bias=TrendBias.BULLISH,
        bias_evidence=("structure bullish",),
        adx_last=None,
        bb_width_now=None,
        bb_width_avg=None,
        nearest_key_reaction=None,
    )
    assert result.regime is MarketRegime.UNCERTAIN
    assert result.independent_streams == 1


def test_trend_and_adx_agree_on_trending_up() -> None:
    result = classify_regime(
        trend_bias=TrendBias.BULLISH,
        bias_evidence=("HH/HL sequence",),
        adx_last=30.0,
        bb_width_now=None,
        bb_width_avg=None,
        nearest_key_reaction=None,
    )
    assert result.regime is MarketRegime.TRENDING_UP
    assert result.independent_streams == 2


def test_neutral_trend_and_low_adx_gives_range() -> None:
    result = classify_regime(
        trend_bias=TrendBias.NEUTRAL,
        bias_evidence=("no clear sequence",),
        adx_last=10.0,
        bb_width_now=None,
        bb_width_avg=None,
        nearest_key_reaction=None,
    )
    assert result.regime is MarketRegime.RANGE


def test_sweep_reaction_outweighs_trend_vote() -> None:
    result = classify_regime(
        trend_bias=TrendBias.BULLISH,
        bias_evidence=("HH/HL sequence",),
        adx_last=None,
        bb_width_now=None,
        bb_width_avg=None,
        nearest_key_reaction=ReactionKind.SWEEP,
    )
    assert result.regime is MarketRegime.LIQUIDITY_SWEEP


def test_expansion_detected_from_bollinger_width() -> None:
    result = classify_regime(
        trend_bias=TrendBias.NEUTRAL,
        bias_evidence=("no clear sequence",),
        adx_last=10.0,
        bb_width_now=3.0,
        bb_width_avg=2.0,
        nearest_key_reaction=None,
    )
    # RANGE gets 2 votes (trend + adx), EXPANSION gets 1 -> RANGE wins by count
    assert result.regime is MarketRegime.RANGE
    assert "expanding" in " ".join(result.evidence)


def test_compression_wins_tiebreak_over_range() -> None:
    result = classify_regime(
        trend_bias=TrendBias.NEUTRAL,
        bias_evidence=("no clear sequence",),
        adx_last=None,
        bb_width_now=1.0,
        bb_width_avg=2.0,
        nearest_key_reaction=None,
    )
    # RANGE=1 (trend only), COMPRESSION=1 (bollinger) -> tied vote counts;
    # COMPRESSION outranks RANGE in the fixed priority order used to break ties.
    assert result.regime is MarketRegime.COMPRESSION


def test_no_evidence_at_all_is_uncertain() -> None:
    result = classify_regime(
        trend_bias=TrendBias.NEUTRAL,
        bias_evidence=(),
        adx_last=None,
        bb_width_now=None,
        bb_width_avg=None,
        nearest_key_reaction=None,
    )
    assert result.regime is MarketRegime.UNCERTAIN
