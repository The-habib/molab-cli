"""
Sliding-Window Rate Limiter & Concurrency Guard for MoLab AI Gateway.
Enforces per-key Requests Per Minute (RPM) and Tokens Per Minute (TPM) limits
with microsecond sliding window precision and standard RFC 6585 headers.
"""

import collections
import threading
import time
from typing import Any, Dict, Optional, Tuple


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter tracking RPM and TPM."""

    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        # key_id -> deque of (timestamp, token_count)
        self._history: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def _prune(self, dq: collections.deque, now: float) -> None:
        """Remove entries older than the window boundary."""
        cutoff = now - self.window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def check_limit(
        self,
        key_id: str,
        rpm_limit: int,
        tpm_limit: int,
        estimated_tokens: int = 1,
    ) -> Tuple[bool, Optional[str], Dict[str, str]]:
        """
        Check if request is permitted under RPM and TPM constraints.
        Returns: (allowed: bool, rejection_reason: Optional[str], headers: Dict[str, str])
        """
        now = time.time()

        with self._lock:
            dq = self._history[key_id]
            self._prune(dq, now)

            current_requests = len(dq)
            current_tokens = sum(item[1] for item in dq)

            # Check RPM
            if current_requests >= rpm_limit:
                oldest_ts = dq[0][0]
                retry_after = max(1, int(oldest_ts + self.window_seconds - now))
                headers = {
                    "x-ratelimit-limit-requests": str(rpm_limit),
                    "x-ratelimit-remaining-requests": "0",
                    "x-ratelimit-reset-requests": str(retry_after),
                    "retry-after": str(retry_after),
                }
                return False, f"Rate limit exceeded: {rpm_limit} requests/min limit reached. Retry in {retry_after}s.", headers

            # Check TPM
            if current_tokens + estimated_tokens > tpm_limit:
                oldest_ts = dq[0][0]
                retry_after = max(1, int(oldest_ts + self.window_seconds - now))
                headers = {
                    "x-ratelimit-limit-tokens": str(tpm_limit),
                    "x-ratelimit-remaining-tokens": str(max(0, tpm_limit - current_tokens)),
                    "x-ratelimit-reset-tokens": str(retry_after),
                    "retry-after": str(retry_after),
                }
                return False, f"Token rate limit exceeded: {tpm_limit} tokens/min limit reached. Retry in {retry_after}s.", headers

            # Allowed -> Record entry
            dq.append((now, estimated_tokens))

            rem_req = max(0, rpm_limit - (current_requests + 1))
            headers = {
                "x-ratelimit-limit-requests": str(rpm_limit),
                "x-ratelimit-remaining-requests": str(rem_req),
                "x-ratelimit-reset-requests": "0",
            }
            return True, None, headers

    def reset(self, key_id: Optional[str] = None) -> None:
        """Clear limiter state."""
        with self._lock:
            if key_id:
                self._history.pop(key_id, None)
            else:
                self._history.clear()


default_rate_limiter = SlidingWindowRateLimiter()
