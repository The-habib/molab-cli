"""
Unit tests for MoLab AI Gateway SlidingWindowRateLimiter.
"""

import time
import pytest

from molab_cli.rate_limiter import SlidingWindowRateLimiter


def test_rpm_limit_enforcement():
    limiter = SlidingWindowRateLimiter(window_seconds=2.0)
    key_id = "test_key_rpm"

    # Allow 3 requests in 2 seconds
    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=3, tpm_limit=10000, estimated_tokens=10)
    assert allowed is True
    assert headers["x-ratelimit-remaining-requests"] == "2"

    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=3, tpm_limit=10000, estimated_tokens=10)
    assert allowed is True
    assert headers["x-ratelimit-remaining-requests"] == "1"

    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=3, tpm_limit=10000, estimated_tokens=10)
    assert allowed is True
    assert headers["x-ratelimit-remaining-requests"] == "0"

    # 4th request must be rejected
    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=3, tpm_limit=10000, estimated_tokens=10)
    assert allowed is False
    assert "Rate limit exceeded" in reason
    assert "retry-after" in headers
    assert headers["x-ratelimit-remaining-requests"] == "0"


def test_tpm_limit_enforcement():
    limiter = SlidingWindowRateLimiter(window_seconds=2.0)
    key_id = "test_key_tpm"

    # Limit to 100 tokens
    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=100, tpm_limit=100, estimated_tokens=60)
    assert allowed is True

    # 50 more tokens exceeds 100 limit (60 + 50 = 110 > 100)
    allowed, reason, headers = limiter.check_limit(key_id, rpm_limit=100, tpm_limit=100, estimated_tokens=50)
    assert allowed is False
    assert "Token rate limit exceeded" in reason
    assert "retry-after" in headers


def test_sliding_window_expiration():
    limiter = SlidingWindowRateLimiter(window_seconds=0.2)
    key_id = "test_key_expire"

    # Consume all 1 request
    allowed, _, _ = limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    assert allowed is True

    # Immediate next request rejected
    allowed, _, _ = limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    assert allowed is False

    # Wait for window to slide past
    time.sleep(0.25)

    # Next request must be allowed again
    allowed, _, _ = limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    assert allowed is True


def test_limiter_reset():
    limiter = SlidingWindowRateLimiter(window_seconds=10.0)
    key_id = "test_reset_key"

    limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    allowed, _, _ = limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    assert allowed is False

    limiter.reset(key_id)
    allowed, _, _ = limiter.check_limit(key_id, rpm_limit=1, tpm_limit=1000, estimated_tokens=10)
    assert allowed is True
