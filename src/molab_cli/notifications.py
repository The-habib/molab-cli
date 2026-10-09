"""
Isolated Webhook Notification Subsystem for MoLab Cloud Pods.
Handles delivery of batch lifecycle events to remote webhooks (generic HTTPS, Discord, Telegram)
with strict credential redaction and isolated failure handling.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional

import httpx

REDACTED_STR = "[REDACTED]"
SENSITIVE_KEY_PATTERNS = [
    "token",
    "cookie",
    "secret",
    "password",
    "authorization",
    "auth",
    "api_key",
    "client_cookie",
    "private_key",
    "credentials",
]

# Regex patterns for stripping embedded tokens in strings
COOKIE_PATTERN = re.compile(r"(__client=)[^;&\s]+", re.IGNORECASE)
BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE)
AUTH_PARAM_PATTERN = re.compile(r"([?&](?:token|key|secret|apiKey)=)[^&\s]+", re.IGNORECASE)


def redact_sensitive_data(val: Any) -> Any:
    """
    Recursively sanitize dictionaries, lists, and strings by replacing sensitive
    credentials, tokens, cookies, and secrets with [REDACTED].
    """
    if isinstance(val, dict):
        sanitized = {}
        for k, v in val.items():
            k_lower = str(k).lower()
            if any(pattern in k_lower for pattern in SENSITIVE_KEY_PATTERNS):
                sanitized[k] = REDACTED_STR
            else:
                sanitized[k] = redact_sensitive_data(v)
        return sanitized

    if isinstance(val, list):
        return [redact_sensitive_data(item) for item in val]

    if isinstance(val, str):
        # Scrub inline token patterns
        sanitized_str = COOKIE_PATTERN.sub(r"\1" + REDACTED_STR, val)
        sanitized_str = BEARER_PATTERN.sub(r"\1" + REDACTED_STR, sanitized_str)
        sanitized_str = AUTH_PARAM_PATTERN.sub(r"\1" + REDACTED_STR, sanitized_str)
        return sanitized_str

    return val


def format_webhook_payload(event_type: str, data: Dict[str, Any], webhook_url: str) -> Dict[str, Any]:
    """
    Format payload specifically for destination webhook provider (Discord, Telegram, or generic JSON).
    """
    sanitized_data = redact_sensitive_data(data)

    # Discord Webhook detection
    if "discord.com/api/webhooks" in webhook_url.lower():
        color_map = {
            "batch_completed": 0x22C55E,  # green
            "task_completed": 0x10B981,   # teal
            "batch_started": 0x3B82F6,    # blue
            "task_started": 0x60A5FA,     # light blue
            "task_failed": 0xEF4444,      # red
            "batch_failed": 0xDC2626,     # dark red
            "batch_cancelled": 0xF59E0B,  # amber
        }
        color = color_map.get(event_type, 0x6B7280)

        title = f"MoLab Event: {event_type.replace('_', ' ').title()}"
        description = sanitized_data.get("message") or f"Event `{event_type}` occurred in batch orchestrator."

        fields = []
        for k, v in sanitized_data.items():
            if k in ("message", "details"):
                continue
            if v is not None:
                val_str = str(v)
                if len(val_str) > 1000:
                    val_str = val_str[:1000] + "..."
                fields.append({"name": str(k), "value": val_str, "inline": True})

        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields[:25],
            "footer": {"text": "MoLab Multi-Pod Orchestrator v2.2"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return {"embeds": [embed]}

    # Generic Webhook payload
    return {
        "event": event_type,
        "timestamp": time.time(),
        "source": "molab-orchestrator",
        "data": sanitized_data,
    }


def send_webhook(
    webhook_url: str,
    event_type: str,
    data: Dict[str, Any],
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """
    Deliver webhook notification to target URL.
    Isolated: Never raises network exceptions that disrupt batch execution.
    """
    if not webhook_url:
        return {"status": "SKIPPED", "status_code": None, "error": "No webhook URL configured"}

    payload = format_webhook_payload(event_type, data, webhook_url)

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "MoLab-Orchestrator/2.2",
                },
            )
            if 200 <= resp.status_code < 300:
                return {
                    "status": "SENT",
                    "status_code": resp.status_code,
                    "error": None,
                }
            else:
                return {
                    "status": "FAILED",
                    "status_code": resp.status_code,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {
            "status": "FAILED",
            "status_code": None,
            "error": str(e),
        }


class NotificationDispatcher:
    """Dispatches lifecycle notifications with persistence tracking."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        events: Optional[List[str]] = None,
        job_manager: Optional[Any] = None,
    ):
        self.webhook_url = webhook_url
        self.events = set(events) if events else {
            "batch_started",
            "batch_completed",
            "batch_failed",
            "batch_cancelled",
            "task_started",
            "task_completed",
            "task_failed",
        }
        self.job_manager = job_manager

    def dispatch(
        self,
        event_type: str,
        data: Dict[str, Any],
        batch_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Dispatch notification if event is configured."""
        if not self.webhook_url or event_type not in self.events:
            return None

        result = send_webhook(self.webhook_url, event_type, data)

        if self.job_manager and hasattr(self.job_manager, "record_notification"):
            try:
                self.job_manager.record_notification(
                    batch_id=batch_id or data.get("batch_id", "unknown"),
                    event_type=event_type,
                    webhook_url=self.webhook_url,
                    payload=data,
                    status=result.get("status", "FAILED"),
                    error_message=result.get("error"),
                )
            except Exception:
                pass

        return result
