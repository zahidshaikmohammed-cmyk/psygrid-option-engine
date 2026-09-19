from __future__ import annotations

from datetime import UTC, datetime

import pytest

from psygrid_option_engine.config.settings import Settings

# 2026-09-18 is a Friday; 05:00 UTC = 10:30 IST, well inside the NORMAL
# session window with the default 09:15-15:30 / cutoff 15:00 settings.
WITHIN_SESSION_UTC = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)
OUTSIDE_SESSION_UTC = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)


@pytest.fixture
def fast_settings() -> Settings:
    """Settings with retries disabled so tests don't sleep through backoff."""
    return Settings(max_retries=0, retry_backoff_seconds=0.01)
