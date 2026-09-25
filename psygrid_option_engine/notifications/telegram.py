"""Telegram notifications for newly-accepted TRADE_READY signals.

This is an isolated, best-effort side channel - never part of the
decision core. Per docs/SAFETY.md, this process never places a broker
order and nothing in this module may interrupt or delay `run_engine.py`'s
trading loop: every public method here catches its own errors and
reports success as a bool return value instead of raising. A Telegram
outage, bad token, or missing configuration must degrade to "no
notification sent", never to a crashed or stalled engine.

Wired in from `run_engine.py::_register_new_active_trades` - the point a
TRADE_READY signal is newly registered as an active trade, i.e. already
past `LifecycleTracker`'s dedup/cooldown guard, not merely a developing
candidate.
"""

from __future__ import annotations

import sys

import httpx

from psygrid_option_engine.config.settings import Settings
from psygrid_option_engine.signals.schema import TradeReadySignal

_TELEGRAM_API_BASE = "https://api.telegram.org"
_REQUEST_TIMEOUT_SECONDS = 10.0


class TelegramNotifier:
    """Disabled (a no-op) unless both PSYGRID_TELEGRAM_BOT_TOKEN and
    PSYGRID_TELEGRAM_CHAT_ID are set - check `enabled` or just call the
    notify methods directly, since they're no-ops themselves when
    disabled."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._chat_id = settings.telegram_chat_id
        self._token = (
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token is not None
            else None
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=_TELEGRAM_API_BASE, timeout=_REQUEST_TIMEOUT_SECONDS
        )

    @property
    def enabled(self) -> bool:
        return bool(self._token) and bool(self._chat_id)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TelegramNotifier:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            response = self._client.post(
                f"/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            print(f"[telegram] notification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return False

    def notify_trade_ready(self, underlying: str, signal: TradeReadySignal) -> bool:
        contract = signal.contract
        execution = signal.execution
        lines = [
            f"{underlying} {contract.strike:g} {contract.option_type} "
            f"- TRADE_READY (Tier {signal.tier})",
            f"Direction: {signal.direction}",
            f"Entry: {execution.entry:.2f}  SL: {execution.stop_loss:.2f}  "
            f"TP: {execution.take_profit:.2f}  R:R: {execution.risk_reward:.2f}",
            f"Security ID: {contract.security_id}  Expiry: {contract.expiry}",
            "Signal-only - no order has been placed.",
        ]
        return self._send("\n".join(lines))

    def send_test_message(self) -> bool:
        return self._send("PSYGRID Option Engine: Telegram notifications are configured correctly.")
