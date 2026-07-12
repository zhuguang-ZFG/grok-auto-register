#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domain matching + source tagging for pool hygiene."""
from __future__ import annotations

import unittest
from pathlib import Path

from pool_policy import (
    domain_matches,
    is_own_email,
    is_own_path,
    tag_pool_source,
    watermark_own_only,
)


class DomainMatchTests(unittest.TestCase):
    def test_exact_and_subdomain(self):
        self.assertTrue(domain_matches("ccwu.cc", "ccwu.cc"))
        self.assertTrue(domain_matches("mail.ccwu.cc", "ccwu.cc"))
        self.assertTrue(domain_matches("a.b.ccwu.cc", "ccwu.cc"))

    def test_rejects_substring_false_friends(self):
        # previously `d in dom` treated these as own
        self.assertFalse(domain_matches("evilccwu.cc", "ccwu.cc"))
        self.assertFalse(domain_matches("notlima.cc.cd", "lima.cc.cd"))
        self.assertFalse(domain_matches("ccwu.cc.evil.com", "ccwu.cc"))
        self.assertFalse(domain_matches("xccwu.cc", "ccwu.cc"))

    def test_evil_ccwu_is_subdomain_ok(self):
        # DNS: evil.ccwu.cc is a real subdomain of ccwu.cc
        self.assertTrue(domain_matches("evil.ccwu.cc", "ccwu.cc"))


class OwnEmailPathTests(unittest.TestCase):
    def test_empty_own_fail_closed(self):
        self.assertFalse(is_own_email("a@b.com", {}))
        self.assertFalse(is_own_path("xai-a@b.com.json", {}))

    def test_own_email_and_path(self):
        cfg = {"defaultDomains": "zhuguang.ccwu.cc,lima.cc.cd"}
        self.assertTrue(is_own_email("u@zhuguang.ccwu.cc", cfg))
        self.assertTrue(is_own_path(Path("xai-u@zhuguang.ccwu.cc.json"), cfg))
        self.assertFalse(is_own_email("u@evilccwu.cc", cfg))
        self.assertFalse(is_own_path("xai-u@notlima.cc.cd.json", cfg))

    def test_tag_source(self):
        cfg = {"defaultDomains": "baoxia.top"}
        self.assertEqual(tag_pool_source({"email": "a@baoxia.top"}, cfg)["source"], "own")
        self.assertEqual(tag_pool_source({"email": "a@other.com"}, cfg)["source"], "buffer")
        self.assertEqual(tag_pool_source({}, cfg)["source"], "buffer")


class WatermarkFlagTests(unittest.TestCase):
    def test_default_true(self):
        self.assertTrue(watermark_own_only({}))

    def test_string_false(self):
        self.assertFalse(watermark_own_only({"pool_watermark_own_only": "false"}))


if __name__ == "__main__":
    unittest.main()
