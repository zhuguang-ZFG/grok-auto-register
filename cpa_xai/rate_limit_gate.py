#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process-wide rate-limit gate for xAI OAuth / chat probe traffic.

Inspired by community grok-free-register ``GlobalRateLimitGate``:
  - On 429 / rate-limit trip, only ONE confirmation probe may run after cooldown.
  - Cooldown grows 1.5x per consecutive trip (cap 300s) so a cleared probe does
    not immediately re-enter a tight loop that re-trips the upstream limit.
  - Successful probe clears the trip and slowly decays consecutive counter.

Thread-safe for ThreadPoolExecutor refreshers (keepalive / refresh_pool /
import probe). Async callers can wrap ``wait_for_permission`` via
``asyncio.to_thread``.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

Clock = Callable[[], float]


class GlobalRateLimitGate:
    """Single-process cooldown with exactly one confirmation probe."""

    BASE_COOLDOWN = 60.0
    MAX_COOLDOWN = 300.0
    GROWTH = 1.5

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        base_cooldown: float | None = None,
    ) -> None:
        self._clock: Clock = clock or time.monotonic
        self._lock = threading.Condition()
        self._tripped_at: float | None = None
        self._next_probe_at = 0.0
        self._probe_token: object | None = None
        self._closed = False
        self.base_cooldown = float(
            self.BASE_COOLDOWN if base_cooldown is None else base_cooldown
        )
        self._current_cooldown = self.base_cooldown
        self._consecutive_trips = 0

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped_at is not None

    @property
    def current_cooldown(self) -> float:
        with self._lock:
            return float(self._current_cooldown)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def wait_for_permission(self, *, timeout: float | None = None) -> object | None:
        """Block until traffic is allowed.

        Returns:
          - None: free flight (not in cooldown) — caller may proceed normally.
          - probe_token (object): this caller is the single confirmation probe.
        Raises:
          RuntimeError if gate was closed while waiting.
        """
        deadline = None if timeout is None else self._clock() + max(0.0, timeout)
        with self._lock:
            while True:
                if self._closed:
                    raise RuntimeError("rate_limit_gate_closed")
                if self._tripped_at is None:
                    return None
                now = self._clock()
                if self._probe_token is None and now >= self._next_probe_at:
                    self._probe_token = object()
                    return self._probe_token
                wait_for = max(0.0, self._next_probe_at - now)
                if deadline is not None:
                    remain = deadline - now
                    if remain <= 0:
                        raise TimeoutError("rate_limit_gate_wait_timeout")
                    wait_for = min(wait_for, remain) if wait_for else remain
                self._lock.wait(timeout=wait_for or 0.05)

    def rate_limited(self, probe_token: object | None = None) -> bool:
        """Record a rate-limit trip. Returns True if this call extended cooldown."""
        with self._lock:
            now = self._clock()
            if self._tripped_at is None:
                self._tripped_at = now
                self._consecutive_trips = 1
            elif probe_token is None or probe_token is not self._probe_token:
                # Non-probe hit while already cooling — ignore (avoid stampede growth)
                return False
            else:
                self._consecutive_trips = max(1, self._consecutive_trips) + 1
            growth = self.GROWTH ** max(0, self._consecutive_trips - 1)
            self._current_cooldown = min(
                self.MAX_COOLDOWN,
                self.base_cooldown * growth,
            )
            self._next_probe_at = now + self._current_cooldown
            self._probe_token = None
            self._lock.notify_all()
            return True

    def authorized(self, probe_token: object | None = None) -> float | None:
        """Clear trip after a successful confirmation probe.

        Returns elapsed cooldown seconds, or None if this call was not the probe.
        """
        with self._lock:
            if self._tripped_at is None:
                return None
            if probe_token is None or probe_token is not self._probe_token:
                return None
            elapsed = max(0.0, self._clock() - self._tripped_at)
            self._tripped_at = None
            self._next_probe_at = 0.0
            self._probe_token = None
            # slow decay of consecutive trips so one success does not wipe history
            self._consecutive_trips = max(0, self._consecutive_trips - 1)
            self._current_cooldown = self.base_cooldown
            self._lock.notify_all()
            return elapsed

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tripped": self._tripped_at is not None,
                "current_cooldown": self._current_cooldown,
                "consecutive_trips": self._consecutive_trips,
                "next_probe_in": max(0.0, self._next_probe_at - self._clock())
                if self._tripped_at is not None
                else 0.0,
            }


# Process-wide default gate for OAuth token refresh / shared xAI traffic.
_DEFAULT_GATE: GlobalRateLimitGate | None = None
_DEFAULT_LOCK = threading.Lock()


def default_gate() -> GlobalRateLimitGate:
    global _DEFAULT_GATE
    with _DEFAULT_LOCK:
        if _DEFAULT_GATE is None:
            _DEFAULT_GATE = GlobalRateLimitGate()
        return _DEFAULT_GATE


def reset_default_gate_for_tests() -> GlobalRateLimitGate:
    """Replace the process default gate (tests only)."""
    global _DEFAULT_GATE
    with _DEFAULT_LOCK:
        _DEFAULT_GATE = GlobalRateLimitGate()
        return _DEFAULT_GATE


def is_rate_limit_payload(status: int, body: Any) -> bool:
    """Heuristic: HTTP 429 or body markers for xAI / gateway rate limits."""
    if int(status or 0) == 429:
        return True
    blob = ""
    if isinstance(body, dict):
        blob = json_blob(body)
    else:
        blob = str(body or "").lower()
    needles = (
        "rate limit",
        "rate_limit",
        "ratelimit",
        "too many requests",
        "temporarily rate limited",
        "slow down",
        "concurrency limit",
    )
    return any(n in blob for n in needles)


def json_blob(body: dict) -> str:
    parts = [
        str(body.get("error") or ""),
        str(body.get("error_description") or ""),
        str(body.get("message") or ""),
        str(body.get("code") or ""),
    ]
    return " ".join(parts).lower()
