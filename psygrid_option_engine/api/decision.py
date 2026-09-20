"""The single deterministic decision function: `MarketSnapshot -> Signal`.

Both the live runtime (`api/runtime.py`) and the replay driver
(`replay/engine.py`) call this exact function - brief section 32: "LIVE
and REPLAY must share the same decision engine." Nothing here reads the
wall clock, makes a network call, or holds state across calls; every
input needed is in the `MarketSnapshot` and `Settings` passed in.

Pipeline: structure -> (momentum/pullback/volatility/futures/chain/
breadth evidence, per direction) -> authorization/opportunity -> option
selection -> execution engineering -> risk validation -> Signal.
"""

from __future__ import annotations

from typing import Literal

from psygrid_option_engine.authorization.frameworks import FrameworkContext
from psygrid_option_engine.authorization.opportunity import Opportunity, build_opportunities
from psygrid_option_engine.config.settings import Settings, get_settings
from psygrid_option_engine.domain.evidence import EvidenceItem, EvidenceStance
from psygrid_option_engine.domain.snapshot import MarketSnapshot
from psygrid_option_engine.domain.timeframe import Timeframe
from psygrid_option_engine.execution.engine import ExecutionResult, engineer_trade
from psygrid_option_engine.market.breadth import BreadthEvidence, analyze_breadth
from psygrid_option_engine.market.context import ContextAssessment, analyze_context
from psygrid_option_engine.market.divergence import detect_divergences
from psygrid_option_engine.market.futures_analysis import FuturesEvidence, analyze_futures
from psygrid_option_engine.market.indicators import atr as atr_series
from psygrid_option_engine.market.momentum import (
    ImpulseMetrics,
    MomentumQuality,
    assess_momentum_quality,
    compute_impulse,
)
from psygrid_option_engine.market.pullback import PullbackAssessment, classify_pullback
from psygrid_option_engine.market.volatility import RangeModel, compute_range_model
from psygrid_option_engine.options.chain import compute_chain_metrics
from psygrid_option_engine.options.selection import ContractCandidate, SelectionResult, select_contract
from psygrid_option_engine.risk.validation import validate_risk
from psygrid_option_engine.signals.schema import (
    ContractRef,
    DevelopingSetup,
    ExecutionPlan,
    ExecutionQuality,
    NoTradeSignal,
    Signal,
    TradeReadySignal,
)
from psygrid_option_engine.structure.engine import analyze_structure
from psygrid_option_engine.structure.types import StructureAnalysis, StructureState

Direction = Literal["CALL", "PUT"]


def decide(snapshot: MarketSnapshot, *, settings: Settings | None = None) -> Signal:
    settings = settings or get_settings()
    session_window = settings.session_window()

    if not snapshot.data_quality.critical_endpoints_ok:
        return NoTradeSignal(
            decision_timestamp=snapshot.as_of,
            underlying=snapshot.underlying,  # type: ignore[arg-type]
            data_quality=snapshot.data_quality,
            reasons=_critical_data_reasons(snapshot),
        )

    underlying = snapshot.underlying_snapshot
    ltp = underlying.ltp.value if underlying else None
    if underlying is None or ltp is None:
        return NoTradeSignal(
            decision_timestamp=snapshot.as_of,
            underlying=snapshot.underlying,  # type: ignore[arg-type]
            data_quality=snapshot.data_quality,
            reasons=["underlying last traded price is unavailable"],
        )

    structure_analysis = analyze_structure(snapshot, session_window=session_window)
    m1 = underlying.candles.get(Timeframe.M1, ())
    closed_m1 = sorted((c for c in m1 if c.is_closed), key=lambda c: c.start)

    # Intraday (per-minute-bar) ATR - the correct scale for normalizing a
    # 5-minute impulse. NOT the same thing as a daily ATR (see below); a
    # per-bar ATR is 1-2 orders of magnitude smaller and must never be
    # substituted for it.
    intraday_atr = _latest(atr_series(closed_m1, period=14)) if len(closed_m1) >= 14 else None
    impulse = compute_impulse(closed_m1, window=5, atr_value=intraday_atr) if len(closed_m1) >= 5 else None
    momentum_quality = assess_momentum_quality(impulse)
    pullback = _derive_pullback(structure_analysis.structure, ltp)

    # The canonical structural session open (structure/levels.py already
    # falls back to the first closed M1 candle's open when the raw
    # underlying payload has no top-level day-open field - which is the
    # normal case in production, confirmed against a real payload). Using
    # the raw field directly here would make price_change_pct silently
    # None in production even though a perfectly good derived open exists.
    day_open = structure_analysis.levels.today_open
    price_change_pct = ((ltp - day_open) / day_open * 100) if day_open else None

    # Genuine daily-bar ATR, computed only from D1 candles - a single-day
    # live snapshot typically has none yet (see structure/levels.py's same
    # limitation), so this is honestly None until replay/ accumulates
    # multi-day history rather than being faked from intraday bars.
    d1 = underlying.candles.get(Timeframe.D1, ())
    closed_d1 = sorted((c for c in d1 if c.is_closed), key=lambda c: c.start)
    daily_atr = _latest(atr_series(closed_d1, period=14)) if len(closed_d1) >= 14 else None

    vix_value = snapshot.vix.value.value if (snapshot.vix and snapshot.vix.value.available) else None
    range_model = compute_range_model(
        levels=structure_analysis.levels,
        atr_daily=daily_atr,
        vix=vix_value,
        ltp=ltp,
        as_of=snapshot.as_of,
        session_window=session_window,
    )

    ctx = FrameworkContext(
        regime=structure_analysis.regime,
        structure=structure_analysis.structure,
        levels=structure_analysis.levels,
        liquidity_zones=structure_analysis.liquidity_zones,
        level_reactions=structure_analysis.level_reactions,
        ltp=ltp,
        vwap=structure_analysis.vwap,
        vwap_relation=structure_analysis.vwap_relation,
        impulse=impulse,
        momentum_quality=momentum_quality,
        pullback=pullback,
        range_model=range_model,
    )

    # Not direction-specific (delayed macro series / news are the same
    # regardless of which side is being evaluated) - computed once and
    # reused per direction.
    context_assessment = analyze_context(snapshot.global_context, snapshot.news, as_of=snapshot.as_of)

    evidence_by_direction: dict[Direction, list[EvidenceItem]] = {}
    liquidity_quality_by_direction: dict[Direction, str] = {}
    selection_by_direction: dict[Direction, SelectionResult] = {}

    for direction in ("CALL", "PUT"):
        selection = select_contract(snapshot.options, direction=direction, underlying_ltp=ltp, depth=snapshot.depth)
        selection_by_direction[direction] = selection
        liquidity_quality_by_direction[direction] = (
            selection.selected.depth.liquidity_quality if selection.selected else "UNKNOWN"
        )
        evidence_by_direction[direction] = _direction_evidence(
            snapshot,
            direction=direction,
            underlying_ltp=ltp,
            price_change_pct=price_change_pct,
            impulse=impulse,
            momentum_quality=momentum_quality,
            selection=selection,
            context=context_assessment,
        )

    independent_streams = structure_analysis.regime.independent_streams + sum(
        1
        for available in (
            snapshot.futures is not None,
            snapshot.options is not None and bool(snapshot.options.legs),
            snapshot.breadth is not None,
            impulse is not None,
        )
        if available
    )

    scan = build_opportunities(
        underlying=snapshot.underlying,
        ctx=ctx,
        evidence_by_direction=evidence_by_direction,
        data_quality_overall=snapshot.data_quality.overall,
        liquidity_quality_by_direction=liquidity_quality_by_direction,
        independent_streams=independent_streams,
    )

    market_state = _market_state_summary(snapshot, structure_analysis, ltp, price_change_pct, range_model)
    structure_dict = _structure_summary(structure_analysis)

    if scan.best is None:
        return _no_trade_signal(snapshot, scan.best_developing, market_state, structure_dict)

    opportunity = scan.best
    selection = selection_by_direction[opportunity.direction]
    candidate = selection.selected

    if candidate is None:
        return _no_trade_signal(
            snapshot,
            opportunity,
            market_state,
            structure_dict,
            extra_reason="tier reached but no valid contract survived selection",
        )
    if candidate.leg.expiry is None:
        return _no_trade_signal(
            snapshot,
            opportunity,
            market_state,
            structure_dict,
            extra_reason="selected contract has no known expiry - cannot finalize trade",
        )

    execution = engineer_trade(
        candidate.leg,
        direction=opportunity.direction,
        underlying_ltp=ltp,
        invalidation_level=opportunity.invalidation_level,
        target_level=opportunity.target_level,
    )

    risk_result = validate_risk(
        plan=execution.plan,
        candidate=candidate,
        tier=opportunity.tier,
        data_quality_overall=snapshot.data_quality.overall,
        session_window=session_window,
        as_of=snapshot.as_of,
        expiry=candidate.leg.expiry,
    )

    if not risk_result.passed or execution.plan is None:
        reasons = list(risk_result.failed_reasons)
        if execution.rejection_reason:
            reasons.append(f"execution: {execution.rejection_reason}")
        return _no_trade_signal(snapshot, opportunity, market_state, structure_dict, extra_reasons=reasons)

    return _trade_ready_signal(snapshot, opportunity, candidate, execution, market_state, structure_dict)


# --- helpers --------------------------------------------------------------


def _latest(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _critical_data_reasons(snapshot: MarketSnapshot) -> list[str]:
    reasons: list[str] = []
    for name in ("underlying", "options", "depth"):
        status = snapshot.data_quality.per_source.get(name)
        if status is not None and status.status != "OK":
            reasons.append(f"critical endpoint issue: {name} ({status.status.lower()})")
        elif name in snapshot.data_quality.unavailable_fields:
            # Fetch itself was fine, but the canonical data actually needed
            # for a decision (e.g. underlying LTP, at least one option leg)
            # could not be extracted from the payload.
            reasons.append(f"critical endpoint issue: {name} (required data not extractable)")
    return reasons or ["critical upstream data unavailable"]


def _derive_pullback(structure_state: StructureState, ltp: float | None) -> PullbackAssessment | None:
    swings = sorted((ls.swing for ls in structure_state.swings), key=lambda s: s.index)
    if len(swings) < 2 or ltp is None:
        return None
    last = swings[-1]
    prior = next((s for s in reversed(swings[:-1]) if s.kind != last.kind), None)
    if prior is None:
        return None

    bullish_impulse = last.price > prior.price
    made_new_extreme = (ltp < prior.price) if bullish_impulse else (ltp > prior.price)
    resumed = (ltp > last.price) if bullish_impulse else (ltp < last.price)

    return classify_pullback(
        impulse_start=prior.price,
        impulse_end=last.price,
        current_price=ltp,
        post_impulse_swing_count=0,
        made_new_extreme_beyond_start=made_new_extreme,
        resumed_beyond_impulse_end=resumed,
    )


def _momentum_evidence(
    impulse: ImpulseMetrics | None, quality: MomentumQuality | None, direction: Direction
) -> EvidenceItem:
    if impulse is None or quality is None:
        detail = "insufficient candle history for momentum"
        return EvidenceItem("momentum", EvidenceStance.UNAVAILABLE, detail)

    bullish_move = impulse.size > 0
    aligned = bullish_move == (direction == "CALL")
    if quality in (MomentumQuality.STRONG, MomentumQuality.MODERATE):
        stance = EvidenceStance.SUPPORTIVE if aligned else EvidenceStance.CONFLICTING
    elif quality is MomentumQuality.WEAK:
        stance = EvidenceStance.NEUTRAL
    else:  # DECAYING
        stance = EvidenceStance.CONFLICTING if aligned else EvidenceStance.NEUTRAL

    return EvidenceItem("momentum", stance, f"momentum {quality.value}, impulse size {impulse.size:+.2f}")


def _depth_evidence(selection: SelectionResult) -> EvidenceItem:
    if selection.selected is None:
        detail = "no contract survived liquidity/spread constraints"
        return EvidenceItem("option_depth", EvidenceStance.UNAVAILABLE, detail)
    quality = selection.selected.depth.liquidity_quality
    stance = {
        "GOOD": EvidenceStance.SUPPORTIVE,
        "FAIR": EvidenceStance.NEUTRAL,
        "POOR": EvidenceStance.CONFLICTING,
        "UNKNOWN": EvidenceStance.UNAVAILABLE,
    }[quality]
    return EvidenceItem("option_depth", stance, f"selected contract liquidity: {quality}")


def _context_evidence(context: ContextAssessment) -> EvidenceItem:
    if context.event_risk_flag:
        # A known scheduled/breaking macro event is a genuine reason for
        # caution regardless of direction - this is the one place
        # analyze_context's event_risk_flag actually gets acted on, rather
        # than computed and silently discarded.
        return EvidenceItem(
            "global_context", EvidenceStance.CONFLICTING, "event-risk flagged: " + "; ".join(context.notes)
        )
    detail = "; ".join(context.notes) if context.notes else "no macro/news context available"
    return EvidenceItem("global_context", context.stance, detail)


def _divergence_evidence(
    *,
    price_change_pct: float | None,
    breadth_ev: BreadthEvidence,
    impulse: ImpulseMetrics | None,
    futures_ev: FuturesEvidence,
    direction: Direction,
) -> list[EvidenceItem]:
    divergences = detect_divergences(
        price_change_pct=price_change_pct, breadth=breadth_ev, momentum=impulse, futures=futures_ev,
        direction=direction,
    )
    return [EvidenceItem(f"divergence:{d.kind.value}", EvidenceStance.CONFLICTING, d.description) for d in divergences]


def _direction_evidence(
    snapshot: MarketSnapshot,
    *,
    direction: Direction,
    underlying_ltp: float,
    price_change_pct: float | None,
    impulse: ImpulseMetrics | None,
    momentum_quality: MomentumQuality | None,
    selection: SelectionResult,
    context: ContextAssessment,
) -> list[EvidenceItem]:
    futures_ev = analyze_futures(
        snapshot.futures, underlying_ltp=underlying_ltp, price_change_pct=price_change_pct, direction=direction
    )
    chain_ev = compute_chain_metrics(snapshot.options, underlying_ltp=underlying_ltp, direction=direction)
    breadth_ev = analyze_breadth(
        snapshot.breadth, snapshot.sectors, direction=direction, underlying=snapshot.underlying
    )

    items = [
        EvidenceItem("futures", futures_ev.stance, futures_ev.detail),
        EvidenceItem("option_chain", chain_ev.stance, chain_ev.detail),
        EvidenceItem("breadth", breadth_ev.stance, breadth_ev.detail),
        _momentum_evidence(impulse, momentum_quality, direction),
        _depth_evidence(selection),
        _context_evidence(context),
    ]
    items.extend(
        _divergence_evidence(
            price_change_pct=price_change_pct, breadth_ev=breadth_ev, impulse=impulse, futures_ev=futures_ev,
            direction=direction,
        )
    )
    return items


def _range_source(range_model: RangeModel) -> str | None:
    if range_model.atr_daily is not None and range_model.vix_implied_daily_move is not None:
        return "ATR+VIX blend"
    if range_model.atr_daily is not None:
        return "ATR only (VIX unavailable)"
    if range_model.vix_implied_daily_move is not None:
        return "VIX-implied only (daily ATR unavailable)"
    return None


def _market_state_summary(
    snapshot: MarketSnapshot,
    structure_analysis: StructureAnalysis,
    ltp: float,
    price_change_pct: float | None,
    range_model: RangeModel,
) -> dict:
    levels = structure_analysis.levels
    week_range_available = levels.current_week_high is not None and levels.current_week_low is not None
    return {
        "ltp": ltp,
        "price_change_pct": price_change_pct,
        "vwap": structure_analysis.vwap,
        "vwap_relation": structure_analysis.vwap_relation,
        "session_high": levels.today_high,
        "session_low": levels.today_low,
        # Never a calibrated probability (brief section 33) - an estimate
        # blending the underlying's own ATR with a VIX-implied daily move,
        # scaled by remaining-session time. None (with range_source=None)
        # is the honest answer when neither ATR nor VIX is available yet,
        # not a fabricated number.
        "expected_range_upper": range_model.upper_boundary,
        "expected_range_lower": range_model.lower_boundary,
        "expected_daily_range": range_model.expected_daily_range,
        "expected_remaining_range": range_model.expected_remaining_range,
        "range_source": _range_source(range_model),
        "atr_daily_component": range_model.atr_daily,
        "vix_implied_daily_move_component": range_model.vix_implied_daily_move,
        "session_range_so_far": range_model.session_range_so_far,
        "range_utilization_pct": range_model.range_utilization_pct,
        "time_remaining_minutes": range_model.time_remaining_minutes,
        "current_week_high": levels.current_week_high,
        "current_week_low": levels.current_week_low,
        # Never fabricated from a single session's candles - see
        # structure/levels.py: current_week_high/low only come from
        # genuine multi-day D1 history. Truthfully reported as
        # unavailable/insufficient history rather than silently presenting
        # today's range as if it were the week's.
        "week_range_status": "AVAILABLE" if week_range_available else "INSUFFICIENT_HISTORY",
        "data_quality": snapshot.data_quality.overall,
    }


def _structure_summary(structure_analysis: StructureAnalysis) -> dict:
    return {
        "regime": structure_analysis.regime.regime.value,
        "regime_evidence": list(structure_analysis.regime.evidence),
        "trend_bias": structure_analysis.structure.trend_bias.value,
        "invalidation_long": structure_analysis.structure.invalidation_long,
        "invalidation_short": structure_analysis.structure.invalidation_short,
        "liquidity_zones": [
            {"kind": z.kind.value, "level": z.level, "note": z.note} for z in structure_analysis.liquidity_zones
        ],
    }


def _developing_setup_from(opportunity: Opportunity) -> DevelopingSetup:
    upgrade = "; ".join(opportunity.tier.missing) if opportunity.tier.missing else "already meets this tier's bar"
    return DevelopingSetup(
        framework=opportunity.framework.framework.value,
        direction=opportunity.direction,  # type: ignore[arg-type]
        tier=int(opportunity.tier.tier),
        tier_label=opportunity.tier.label,
        evidence_summary=list(opportunity.evidence_summary),
        missing_confirmation=list(opportunity.tier.missing),
        upgrade_condition=upgrade,
        invalidation_condition=f"underlying trades through {opportunity.invalidation_level:g}",
    )


def _no_trade_signal(
    snapshot: MarketSnapshot,
    best_developing: Opportunity | None,
    market_state: dict,
    structure_dict: dict,
    *,
    extra_reason: str | None = None,
    extra_reasons: list[str] | None = None,
) -> NoTradeSignal:
    reasons: list[str] = []
    setup: DevelopingSetup | None = None
    tier = 0

    if best_developing is not None:
        setup = _developing_setup_from(best_developing)
        tier = setup.tier
        reasons.extend(best_developing.evidence_summary)
        if best_developing.tier.missing:
            reasons.append("missing for next tier: " + "; ".join(best_developing.tier.missing))
        if best_developing.conflicts:
            reasons.extend(best_developing.conflicts)
    else:
        reasons.append("no strategy framework is currently applicable to the observed regime")
        reasons.extend(structure_dict.get("regime_evidence", []))

    if extra_reason:
        reasons.append(extra_reason)
    if extra_reasons:
        reasons.extend(extra_reasons)
    if not reasons:
        reasons = ["no actionable opportunity currently"]

    return NoTradeSignal(
        decision_timestamp=snapshot.as_of,
        underlying=snapshot.underlying,  # type: ignore[arg-type]
        data_quality=snapshot.data_quality,
        tier=tier,
        market_state=market_state,
        structure=structure_dict,
        reasons=reasons,
        best_developing_setup=setup,
    )


def _trade_ready_signal(
    snapshot: MarketSnapshot,
    opportunity: Opportunity,
    candidate: ContractCandidate,
    execution: ExecutionResult,
    market_state: dict,
    structure_dict: dict,
) -> TradeReadySignal:
    assert execution.plan is not None
    plan = execution.plan
    leg = candidate.leg
    assert leg.expiry is not None  # guaranteed by the caller's guard before invoking this function

    contract = ContractRef(
        security_id=leg.security_id,
        symbol=leg.symbol,
        underlying=snapshot.underlying,  # type: ignore[arg-type]
        expiry=leg.expiry,
        strike=leg.strike,
        option_type=leg.option_type,
    )
    execution_plan = ExecutionPlan(
        entry=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        risk_reward=plan.risk_reward,
        structural_invalidation=plan.structural_invalidation_description,
        underlying_invalidation_level=plan.structural_invalidation_underlying,
    )
    execution_quality = ExecutionQuality(
        spread_pct=candidate.depth.spread_pct or 0.0,
        depth_assessment=candidate.depth.liquidity_quality,
        liquidity_assessment=candidate.depth.liquidity_quality,
    )

    reasons = list(opportunity.evidence_summary) + [opportunity.framework.trigger]

    return TradeReadySignal(
        decision_timestamp=snapshot.as_of,
        underlying=snapshot.underlying,  # type: ignore[arg-type]
        data_quality=snapshot.data_quality,
        tier=int(opportunity.tier.tier),
        market_state=market_state,
        structure=structure_dict,
        authorization={
            "framework": opportunity.framework.framework.value,
            "tier_label": opportunity.tier.label,
            "tier_met": list(opportunity.tier.met),
        },
        option_analysis={
            "selected_reasons": list(candidate.reasons),
            "rejected_candidates": [],
        },
        risk={"checks": "passed"},
        reasons=reasons,
        direction=opportunity.direction,  # type: ignore[arg-type]
        contract=contract,
        execution=execution_plan,
        execution_quality=execution_quality,
    )
