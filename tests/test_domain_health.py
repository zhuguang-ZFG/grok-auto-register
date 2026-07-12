#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

import domain_health as dh


class DomainHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {
            "domain_health_enabled": True,
            "domain_health_path": str(Path(self.tmp.name) / "dh.json"),
            "domain_health_fail_streak_demote": 3,
            "domain_health_demote_sec": 600,
            "domain_health_min_samples": 3,
            "domain_health_min_success_rate": 0.3,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_and_fail_stats(self):
        dh.record_success("a@lima.cc.cd", cfg=self.cfg)
        dh.record_fail("b@lima.cc.cd", reason="turnstile", cfg=self.cfg)
        snap = dh.snapshot(self.cfg)
        ent = snap["domains"]["lima.cc.cd"]
        self.assertEqual(ent["success"], 1)
        self.assertEqual(ent["fail"], 1)
        self.assertFalse(ent["demoted"])

    def test_demote_on_fail_streak(self):
        for _ in range(3):
            dh.record_fail("x@zhuguang.de5.net", reason="mail", cfg=self.cfg)
        self.assertTrue(dh.is_demoted("zhuguang.de5.net", self.cfg))
        active = dh.filter_active_domains(
            ["zhuguang.de5.net", "lima.cc.cd"], cfg=self.cfg
        )
        self.assertEqual(active, ["lima.cc.cd"])

    def test_filter_fallback_when_all_demoted(self):
        for _ in range(3):
            dh.record_fail("only@baoxia.top", cfg=self.cfg)
        active = dh.filter_active_domains(["baoxia.top"], cfg=self.cfg)
        self.assertEqual(active, ["baoxia.top"])

    def test_classify_fail_reason(self):
        self.assertEqual(dh.classify_fail_reason("Turnstile token empty"), "turnstile")
        self.assertEqual(dh.classify_fail_reason("未找到邮箱输入框"), "email_input")
        self.assertEqual(dh.classify_fail_reason("something else"), "other")


class CliproxyRoutingTests(unittest.TestCase):
    def test_apply_profile_roundtrip(self):
        from set_cliproxy_routing import apply_profile, parse_routing, detect_profile

        sample = (
            'host: "127.0.0.1"\n'
            "routing:\n"
            "  strategy: round-robin\n"
            "  session-affinity: false\n"
        )
        cache = apply_profile(sample, "cache")
        p = parse_routing(cache)
        self.assertEqual(p["session_affinity"], "true")
        self.assertEqual(detect_profile(p), "cache")
        pool = apply_profile(cache, "pool")
        p2 = parse_routing(pool)
        self.assertEqual(p2["session_affinity"], "false")
        self.assertEqual(detect_profile(p2), "pool")


if __name__ == "__main__":
    unittest.main()
