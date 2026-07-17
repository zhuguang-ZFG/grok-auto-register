#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from cpa_xai.rate_limit_gate import (
    GlobalRateLimitGate,
    is_rate_limit_payload,
    reset_default_gate_for_tests,
)


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RateLimitGateTests(unittest.TestCase):
    def test_free_flight_returns_none(self) -> None:
        clock = FakeClock()
        gate = GlobalRateLimitGate(clock=clock, base_cooldown=10.0)
        self.assertIsNone(gate.wait_for_permission())
        self.assertFalse(gate.tripped)

    def test_trip_then_single_probe_then_authorized(self) -> None:
        clock = FakeClock()
        gate = GlobalRateLimitGate(clock=clock, base_cooldown=10.0)
        self.assertTrue(gate.rate_limited())
        self.assertTrue(gate.tripped)
        # Before cooldown: wait would block; advance first
        clock.advance(10.0)
        token = gate.wait_for_permission()
        self.assertIsNotNone(token)
        # Second waiter cannot get another probe token while first holds it
        # (simulate by checking snapshot still tripped)
        self.assertTrue(gate.tripped)
        elapsed = gate.authorized(token)
        self.assertIsNotNone(elapsed)
        self.assertFalse(gate.tripped)

    def test_probe_trip_grows_cooldown(self) -> None:
        clock = FakeClock()
        gate = GlobalRateLimitGate(clock=clock, base_cooldown=10.0)
        gate.rate_limited()
        clock.advance(10.0)
        tok = gate.wait_for_permission()
        gate.rate_limited(tok)
        self.assertGreater(gate.current_cooldown, 10.0)

    def test_is_rate_limit_payload(self) -> None:
        self.assertTrue(is_rate_limit_payload(429, {}))
        self.assertTrue(is_rate_limit_payload(200, {"error": "rate_limit exceeded"}))
        self.assertFalse(is_rate_limit_payload(200, {"error": "ok"}))

    def test_default_gate_singleton_reset(self) -> None:
        g1 = reset_default_gate_for_tests()
        g2 = reset_default_gate_for_tests()
        self.assertIsNot(g1, g2)


if __name__ == "__main__":
    unittest.main()
