from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from psygrid_option_engine.config.settings import Settings
from psygrid_option_engine.notifications.telegram import TelegramNotifier
from psygrid_option_engine.signals.schema import (
    ContractRef,
    DataQuality,
    ExecutionPlan,
    ExecutionQuality,
    TradeReadySignal,
)

NOW = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def _trade_ready_signal() -> TradeReadySignal:
    return TradeReadySignal(
        decision_timestamp=NOW,
        underlying="NIFTY",
        data_quality=DataQuality(overall="GOOD", critical_endpoints_ok=True),
        tier=2,
        reasons=["structure bullish", "momentum strong"],
        direction="CALL",
        contract=ContractRef(
            security_id="CE1", symbol="NIFTY24550CE", underlying="NIFTY",
            expiry=date(2026, 9, 25), strike=24550, option_type="CE",
        ),
        execution=ExecutionPlan(
            entry=120.0, stop_loss=90.0, take_profit=170.0, risk_reward=1.67,
            structural_invalidation="underlying CALL thesis invalidates at 24478",
            underlying_invalidation_level=24478.0,
        ),
        execution_quality=ExecutionQuality(spread_pct=0.8, depth_assessment="GOOD", liquidity_assessment="GOOD"),
    )


def _enabled_settings() -> Settings:
    return Settings(telegram_bot_token="test-token", telegram_chat_id="12345")


def _notifier(handler, settings: Settings | None = None) -> TelegramNotifier:
    client = httpx.Client(base_url="https://api.telegram.org", transport=httpx.MockTransport(handler))
    return TelegramNotifier(settings or _enabled_settings(), client=client)


def test_disabled_without_credentials_never_calls_network() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    notifier = _notifier(handler, settings=Settings())
    assert notifier.enabled is False
    assert notifier.notify_trade_ready("NIFTY", _trade_ready_signal()) is False
    assert notifier.send_test_message() is False
    assert calls["n"] == 0
    notifier.close()


def test_enabled_when_both_token_and_chat_id_set() -> None:
    notifier = _notifier(lambda r: httpx.Response(200, json={"ok": True}))
    assert notifier.enabled is True
    notifier.close()


@pytest.mark.parametrize(
    "settings",
    [Settings(telegram_chat_id="12345"), Settings(telegram_bot_token="test-token")],
    ids=["token missing", "chat_id missing"],
)
def test_disabled_when_only_one_credential_set(settings: Settings) -> None:
    notifier = _notifier(lambda r: httpx.Response(200, json={"ok": True}), settings=settings)
    assert notifier.enabled is False
    notifier.close()


def test_notify_trade_ready_posts_expected_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    notifier = _notifier(handler)
    ok = notifier.notify_trade_ready("NIFTY", _trade_ready_signal())

    assert ok is True
    assert captured["url"] == "https://api.telegram.org/bottest-token/sendMessage"
    import json

    payload = json.loads(captured["body"])
    assert payload["chat_id"] == "12345"
    assert "NIFTY" in payload["text"]
    assert "TRADE_READY" in payload["text"]
    assert "CE1" in payload["text"]
    assert "Signal-only" in payload["text"]
    notifier.close()


def test_send_test_message_posts_confirmation_text() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    notifier = _notifier(handler)
    assert notifier.send_test_message() is True

    import json

    payload = json.loads(captured["body"])
    assert "configured correctly" in payload["text"]
    notifier.close()


def test_network_failure_never_raises_and_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    notifier = _notifier(handler)
    ok = notifier.notify_trade_ready("NIFTY", _trade_ready_signal())

    assert ok is False  # must degrade gracefully, never raise, never block the trading loop
    notifier.close()


def test_http_error_status_never_raises_and_returns_false() -> None:
    notifier = _notifier(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    assert notifier.notify_trade_ready("NIFTY", _trade_ready_signal()) is False
    notifier.close()


def test_context_manager_closes_owned_client() -> None:
    with TelegramNotifier(_enabled_settings()) as notifier:
        pass
    assert notifier._client.is_closed is True


def test_injected_client_not_closed_by_notifier() -> None:
    client = httpx.Client(base_url="https://api.telegram.org")
    notifier = TelegramNotifier(_enabled_settings(), client=client)
    notifier.close()
    assert client.is_closed is False
    client.close()
