"""
Resilience & Retry Utilities with Exponential Backoff and Jitter.
"""

from __future__ import annotations

import functools
import random
import time
import urllib.error
from typing import Any, Callable, Optional, Sequence, Tuple, Type, TypeVar

from molab_cli.logger import get_logger

logger = get_logger("retry")

T = TypeVar("T")

DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    urllib.error.URLError,
)


def is_transient_network_error(exc: BaseException) -> bool:
    """Check if exception is a transient network or server fault."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (429, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff delay with optional full jitter."""
    delay = min(max_delay, base_delay * (backoff_factor ** attempt))
    if jitter:
        delay = random.uniform(0.1, delay)
    return delay


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Sequence[Type[BaseException]] = DEFAULT_RETRYABLE_EXCEPTIONS,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[BaseException, int, float], None]] = None,
):
    """
    Decorator that retries a function with exponential backoff and jitter.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempts = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    should_retry = False
                    if retry_if is not None:
                        should_retry = retry_if(exc)
                    elif isinstance(exc, tuple(retryable_exceptions)):
                        should_retry = True

                    if not should_retry:
                        raise

                    attempts += 1
                    if attempts >= max_attempts:
                        logger.debug(
                            "Operation %s failed permanently after %d attempts: %s",
                            getattr(func, "__name__", str(func)),
                            attempts,
                            exc,
                            exc_info=True,
                        )
                        raise

                    delay = calculate_backoff_delay(
                        attempt=attempts - 1,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        backoff_factor=backoff_factor,
                        jitter=jitter,
                    )
                    logger.debug(
                        "Operation %s encountered %s; retrying attempt %d/%d in %.2fs",
                        getattr(func, "__name__", str(func)),
                        type(exc).__name__,
                        attempts + 1,
                        max_attempts,
                        delay,
                    )
                    if on_retry:
                        try:
                            on_retry(exc, attempts, delay)
                        except Exception:
                            pass
                    time.sleep(delay)

        return wrapper

    return decorator
