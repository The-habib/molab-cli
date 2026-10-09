"""
Unit tests for isolated Webhook Notifications subsystem.
"""

from unittest.mock import MagicMock, patch

import pytest

from molab_cli.notifications import (
    NotificationDispatcher,
    format_webhook_payload,
    redact_sensitive_data,
    send_webhook,
)


def test_redact_sensitive_dict():
    raw = {
        "user_email": "user@example.com",
        "api_key": "secret_abc123",
        "nested": {
            "password": "supersecretpassword",
            "token": "tok_xyz",
            "public_id": "pod_123",
        },
        "cookies": ["cookie1", "cookie2"],
    }
    redacted = redact_sensitive_data(raw)
    assert redacted["user_email"] == "user@example.com"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["public_id"] == "pod_123"
    assert redacted["cookies"] == "[REDACTED]"


def test_redact_sensitive_string_tokens():
    raw_cookie_str = "Connecting with cookie: __client=eyJhbGciOiJIUzI1NiJ9.test; path=/"
    redacted = redact_sensitive_data(raw_cookie_str)
    assert "__client=[REDACTED]" in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted

    raw_bearer_str = "Authorization: Bearer secret_jwt_token_here for user"
    redacted_bearer = redact_sensitive_data(raw_bearer_str)
    assert "Bearer [REDACTED]" in redacted_bearer
    assert "secret_jwt_token_here" not in redacted_bearer


def test_format_webhook_payload_discord():
    discord_url = "https://discord.com/api/webhooks/12345/token_abc"
    data = {
        "message": "Task completed successfully",
        "task_id": "task_train",
        "token": "should_be_scrubbed",
        "vram_gb": 94.4,
    }
    payload = format_webhook_payload("task_completed", data, discord_url)
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Task Completed" in embed["title"]
    assert embed["color"] == 0x10B981  # teal
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["token"] == "[REDACTED]"
    assert fields["task_id"] == "task_train"


def test_format_webhook_payload_generic():
    generic_url = "https://api.example.com/webhook"
    data = {"task_id": "t1", "status": "COMPLETED"}
    payload = format_webhook_payload("task_completed", data, generic_url)
    assert payload["event"] == "task_completed"
    assert payload["source"] == "molab-orchestrator"
    assert payload["data"]["task_id"] == "t1"


def test_send_webhook_isolated_failure():
    # Attempt to post to an invalid/unreachable URL; must return FAILED without throwing
    res = send_webhook("http://127.0.0.1:59999/unreachable", "test_event", {"foo": "bar"}, timeout=0.5)
    assert res["status"] == "FAILED"
    assert res["error"] is not None


def test_send_webhook_success():
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        res = send_webhook("https://example.com/webhook", "test_event", {"foo": "bar"})
        assert res["status"] == "SENT"
        assert res["status_code"] == 200


def test_notification_dispatcher():
    mock_jm = MagicMock()
    dispatcher = NotificationDispatcher(
        webhook_url="https://example.com/webhook",
        events=["task_completed"],
        job_manager=mock_jm,
    )

    with patch("molab_cli.notifications.send_webhook") as mock_send:
        mock_send.return_value = {"status": "SENT", "status_code": 200, "error": None}

        # Ignored event
        res_ignored = dispatcher.dispatch("task_started", {"task": "1"}, batch_id="batch_01")
        assert res_ignored is None
        assert mock_send.call_count == 0

        # Subscribed event
        res = dispatcher.dispatch("task_completed", {"task": "1"}, batch_id="batch_01")
        assert res["status"] == "SENT"
        assert mock_send.call_count == 1
        assert mock_jm.record_notification.call_count == 1
