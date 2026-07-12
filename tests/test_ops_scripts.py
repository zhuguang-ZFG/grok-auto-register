#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for kill-path ops scripts (no network)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_json_roundtrip(self):
        from pool_policy import atomic_write_json

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "xai-a@b.com.json"
            atomic_write_json(p, {"email": "a@b.com", "disabled": False})
            self.assertTrue(p.is_file())
            self.assertFalse(p.with_suffix(p.suffix + ".tmp").exists())
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(d["email"], "a@b.com")
            self.assertFalse(d["disabled"])


class HardPurgeClassifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hp = _load("hard_purge_pool", "scripts/hard_purge_pool.py")

    def test_hold_quota(self):
        d = {
            "disabled": True,
            "refresh_token": "x",
            "quota_state": {"reason": "free-usage-exhausted"},
        }
        self.assertEqual(self.hp.classify_disabled(d), "hold_quota")

    def test_terminal_revoked(self):
        d = {
            "disabled": True,
            "refresh_token": "x",
            "quota_state": {"reason": "refresh_revoked"},
        }
        self.assertEqual(self.hp.classify_disabled(d), "terminal")

    def test_probe_unknown_disabled(self):
        d = {"disabled": True, "refresh_token": "x"}
        self.assertEqual(self.hp.classify_disabled(d), "probe")

    def test_terminal_no_rt(self):
        d = {"disabled": True}
        self.assertEqual(self.hp.classify_disabled(d), "terminal")


class PreemptiveNeedsRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pr = _load("preemptive_refresh", "scripts/preemptive_refresh.py")

    def test_skips_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "xai-a@b.com.json"
            p.write_text(
                json.dumps({"disabled": True, "refresh_token": "rt", "access_token": "a.b.c"}),
                encoding="utf-8",
            )
            self.assertFalse(self.pr.needs_refresh(p, within_sec=3600))

    def test_needs_refresh_when_no_exp(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "xai-a@b.com.json"
            p.write_text(
                json.dumps(
                    {
                        "disabled": False,
                        "refresh_token": "rt",
                        "access_token": "not.a.jwt",
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(self.pr.needs_refresh(p, within_sec=3600))


class BufferHealthFailSafeTests(unittest.TestCase):
    def test_fail_safe_is_true(self):
        src = (ROOT / "scripts" / "buffer_health_sample.py").read_text(encoding="utf-8")
        self.assertIn("lambda p, c: True", src)
        self.assertNotIn("lambda p, c: False", src)


class RegisterCliWarningTests(unittest.TestCase):
    def test_warning_comment_present(self):
        src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
        self.assertIn("WARNING", src)
        self.assertIn("monkey-patches create_browser_options", src)


if __name__ == "__main__":
    unittest.main()
