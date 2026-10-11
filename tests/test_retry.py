"""
Unit tests for retry mechanism with exponential backoff and jitter.
"""

import time
import urllib.error
import pytest
from molab_cli.retry import (
    calculate_backoff_delay,
    is_transient_network_error,
    retry_with_backoff,
)


def test_calculate_backoff_delay_exponential():
    d0 = calculate_backoff_delay(0, base_delay=1.0, max_delay=10.0, backoff_factor=2.0, jitter=False)
    d1 = calculate_backoff_delay(1, base_delay=1.0, max_delay=10.0, backoff_factor=2.0, jitter=False)
    d2 = calculate_backoff_delay(2, base_delay=1.0, max_delay=10.0, backoff_factor=2.0, jitter=False)
    assert d0 == 1.0
    assert d1 == 2.0
    assert d2 == 4.0

    # Max delay clamping
    d_capped = calculate_backoff_delay(10, base_delay=1.0, max_delay=10.0, backoff_factor=2.0, jitter=False)
    assert d_capped == 10.0


def test_calculate_backoff_delay_jitter():
    for attempt in range(5):
        d = calculate_backoff_delay(attempt, base_delay=2.0, max_delay=20.0, jitter=True)
        max_possible = min(20.0, 2.0 * (2.0 ** attempt))
        assert 0.1 <= d <= max_possible


def test_is_transient_network_error():
    # Transient HTTP errors
    assert is_transient_network_error(urllib.error.HTTPError("http://test", 429, "Too Many Requests", {}, None))
    assert is_transient_network_error(urllib.error.HTTPError("http://test", 502, "Bad Gateway", {}, None))
    assert is_transient_network_error(urllib.error.HTTPError("http://test", 503, "Service Unavailable", {}, None))
    assert is_transient_network_error(urllib.error.HTTPError("http://test", 504, "Gateway Timeout", {}, None))

    # Non-transient HTTP errors
    assert not is_transient_network_error(urllib.error.HTTPError("http://test", 400, "Bad Request", {}, None))
    assert not is_transient_network_error(urllib.error.HTTPError("http://test", 404, "Not Found", {}, None))
    assert not is_transient_network_error(urllib.error.HTTPError("http://test", 401, "Unauthorized", {}, None))

    # Transient socket / OS / URL errors
    assert is_transient_network_error(urllib.error.URLError("Connection reset"))
    assert is_transient_network_error(ConnectionResetError("Peer reset"))
    assert is_transient_network_error(TimeoutError("Timed out"))
    assert is_transient_network_error(OSError("Host unreachable"))

    # Non-transient standard exceptions
    assert not is_transient_network_error(ValueError("Invalid syntax"))
    assert not is_transient_network_error(KeyError("missing_key"))


def test_retry_with_backoff_success_on_retry():
    call_count = 0

    @retry_with_backoff(max_attempts=3, base_delay=0.01, jitter=False)
    def flaky_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Temporary glitch")
        return "success"

    result = flaky_func()
    assert result == "success"
    assert call_count == 3


def test_retry_with_backoff_exceeds_max_attempts():
    call_count = 0

    @retry_with_backoff(max_attempts=3, base_delay=0.01, jitter=False)
    def always_failing():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("Persistent network outage")

    with pytest.raises(ConnectionError, match="Persistent network outage"):
        always_failing()

    assert call_count == 3


def test_retry_with_backoff_non_retryable_raises_immediately():
    call_count = 0

    @retry_with_backoff(max_attempts=3, base_delay=0.01, jitter=False)
    def raises_value_error():
        nonlocal call_count
        call_count += 1
        raise ValueError("Non-retryable logic error")

    with pytest.raises(ValueError, match="Non-retryable logic error"):
        raises_value_error()

    assert call_count == 1
