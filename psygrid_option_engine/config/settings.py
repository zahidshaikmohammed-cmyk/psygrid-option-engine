"""Engine configuration, loaded from environment variables (see .env.example).

No secrets are hard-coded. If upstream auth is ever required, add the field
here as `SecretStr` sourced from env only — never a literal default.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from psygrid_option_engine.config.session import SessionWindow


class EndpointCriticality(StrEnum):
    """See docs/ENDPOINTS.md: critical vs optional endpoint classification."""

    CRITICAL = "CRITICAL"
    OPTIONAL = "OPTIONAL"


# Logical endpoint keys -> criticality. Kept centralized and documented here
# (rather than scattered through client code) so a future upstream-contract
# verification pass has one place to correct. Per-underlying path building
# lives in data/endpoints.py.
CRITICAL_ENDPOINTS: frozenset[str] = frozenset(
    {
        "underlying",  # nifty.json / banknifty.json
        "options",  # nifty-options.json / banknifty-options.json
        "depth",  # nifty-depth.json / banknifty-depth.json
    }
)

OPTIONAL_ENDPOINTS: frozenset[str] = frozenset(
    {
        "indicators",
        "futures",
        "market_breadth",
        "sectors",
        "global_context",
        "rbi_news",
        "india_vix",
        "live",
    }
)


def endpoint_criticality(logical_name: str) -> EndpointCriticality:
    if logical_name in CRITICAL_ENDPOINTS:
        return EndpointCriticality.CRITICAL
    if logical_name in OPTIONAL_ENDPOINTS:
        return EndpointCriticality.OPTIONAL
    raise KeyError(
        f"Unregistered endpoint key {logical_name!r}; add it to "
        "CRITICAL_ENDPOINTS or OPTIONAL_ENDPOINTS in config/settings.py "
        "(see docs/ENDPOINTS.md) before use."
    )


class Settings(BaseSettings):
    """All tunables. Env var prefix: PSYGRID_. Example: PSYGRID_BASE_URL."""

    model_config = SettingsConfigDict(
        env_prefix="PSYGRID_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Upstream connectivity ---
    base_url: str = Field(
        default="http://140.245.226.102:10000",
        description="PSYGRID production upstream base URL.",
    )
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    http_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)

    # --- Freshness tolerances (seconds) ---
    # How old a fetched field's *observed_at* timestamp may be before it is
    # considered STALE rather than OK. Different data types move at
    # different natural cadences, so one global number would either be too
    # strict for slow-moving context data or too loose for the option chain.
    freshness_tolerance_underlying_seconds: float = Field(default=15.0, gt=0)
    freshness_tolerance_options_seconds: float = Field(default=15.0, gt=0)
    freshness_tolerance_depth_seconds: float = Field(default=10.0, gt=0)
    freshness_tolerance_indicators_seconds: float = Field(default=60.0, gt=0)
    freshness_tolerance_futures_seconds: float = Field(default=15.0, gt=0)
    freshness_tolerance_breadth_seconds: float = Field(default=60.0, gt=0)
    freshness_tolerance_context_seconds: float = Field(
        default=6 * 3600.0,
        gt=0,
        description="global-context.json is explicitly delayed macro data; "
        "tolerate hours, not seconds.",
    )
    freshness_tolerance_news_seconds: float = Field(default=24 * 3600.0, gt=0)
    freshness_tolerance_vix_seconds: float = Field(default=30.0, gt=0)

    # --- Decision loop ---
    decision_interval_seconds: float = Field(default=15.0, gt=0)

    # --- Lifecycle re-entry guard (--live mode) ---
    # A resolved (invalidated/targeted/session-expired) setup identity must
    # not immediately resurrect on the very next tick just because the
    # stateless decide() function still sees the same framework/direction/
    # structural-invalidation-level combination. The primary guard is
    # identity-based (signals/lifecycle.py's terminal states never
    # silently reset - a genuinely new setup gets its own key, since the
    # key includes the numeric structural invalidation level). This
    # cooldown is the secondary, explicit safeguard: even the *same*
    # setup identity may only reactivate after this many seconds have
    # passed since it went terminal, guarding against a same-level
    # whipsaw (e.g. a stop-hit followed by an immediate re-trigger at the
    # same structural level a tick later).
    lifecycle_reentry_cooldown_seconds: float = Field(default=300.0, ge=0)

    # --- Session window overrides (see config/session.py for defaults) ---
    market_open: str = Field(default="09:15")
    market_close: str = Field(default="15:30")
    opening_period_minutes: int = Field(default=15, ge=0)
    entry_cutoff: str = Field(default="15:00")
    late_session_start: str = Field(default="14:30")

    supported_underlyings: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")

    # --- Telegram notifications (optional; see notifications/telegram.py) ---
    # Unset by default - notifications are a no-op until both are provided.
    # Never given a literal default per this module's own docstring above.
    telegram_bot_token: SecretStr | None = Field(
        default=None, description="Telegram bot token from @BotFather."
    )
    telegram_chat_id: str | None = Field(
        default=None, description="Telegram chat/channel ID to notify."
    )

    @field_validator("base_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def session_window(self) -> SessionWindow:
        from datetime import time

        def parse(s: str) -> time:
            h, m = s.split(":")
            return time(int(h), int(m))

        return SessionWindow(
            market_open=parse(self.market_open),
            market_close=parse(self.market_close),
            opening_period_minutes=self.opening_period_minutes,
            entry_cutoff=parse(self.entry_cutoff),
            late_session_start=parse(self.late_session_start),
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings. Tests should construct `Settings(...)`
    directly instead of relying on this cache."""
    return Settings()
