from __future__ import annotations

from datetime import UTC, date, datetime

from psygrid_option_engine.domain.evidence import EvidenceStance
from psygrid_option_engine.domain.field import SourcedField
from psygrid_option_engine.domain.snapshot import GlobalContextSeries, NewsItem
from psygrid_option_engine.market.context import analyze_context

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def test_unavailable_when_nothing_present() -> None:
    result = analyze_context([], [], as_of=NOW)
    assert result.stance is EvidenceStance.UNAVAILABLE
    assert result.notes == ()


def test_global_context_notes_include_source_date() -> None:
    series = GlobalContextSeries(
        name="SP500",
        value=SourcedField.of(5000.0, source="x", observed_at=NOW, fetched_at=NOW),
        source_date=date(2026, 9, 17),
    )
    result = analyze_context([series], [], as_of=NOW)
    assert result.stance is EvidenceStance.NEUTRAL
    assert any("2026-09-17" in n for n in result.notes)


def test_event_risk_flag_from_keyword() -> None:
    news = [NewsItem(headline="RBI holds policy rate steady", published_at=NOW, source="rbi_news")]
    result = analyze_context([], news, as_of=NOW)
    assert result.event_risk_flag is True


def test_no_event_risk_for_benign_headline() -> None:
    news = [NewsItem(headline="Markets open flat in early trade", published_at=NOW, source="rbi_news")]
    result = analyze_context([], news, as_of=NOW)
    assert result.event_risk_flag is False
