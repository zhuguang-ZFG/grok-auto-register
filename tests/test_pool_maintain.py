#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for pool_maintain.RefillGate."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pool_maintain import RefillGate


class RefillGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_state_file = RefillGate.STATE_FILE
        self.tmpdir = Path(tempfile.mkdtemp())
        RefillGate.STATE_FILE = self.tmpdir / "_pool_refill_state.json"

    def tearDown(self) -> None:
        RefillGate.STATE_FILE = self._orig_state_file
        # ignore cleanup errors on Windows
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, data: dict) -> None:
        RefillGate.STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_fresh_state_allows_refill(self) -> None:
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 5, "pool_refill_cooldown_sec": 1800}
        gate = RefillGate(cfg)
        allowed, reason = gate.check()
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_disabled_config_denies(self) -> None:
        cfg = {"pool_auto_refill": False}
        gate = RefillGate(cfg)
        allowed, reason = gate.check()
        self.assertFalse(allowed)
        self.assertEqual(reason, "pool_auto_refill=false")

    def test_no_auto_flag_denies(self) -> None:
        cfg = {"pool_auto_refill": True}
        gate = RefillGate(cfg)
        allowed, reason = gate.check(no_auto=True)
        self.assertFalse(allowed)
        self.assertEqual(reason, "--no-auto-refill")

    def test_force_bypasses_gate(self) -> None:
        cfg = {"pool_auto_refill": False}
        gate = RefillGate(cfg)
        allowed, reason = gate.check(force=True)
        self.assertTrue(allowed)
        self.assertEqual(reason, "force-refill")

    def test_cooldown_blocks_second_refill(self) -> None:
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 5, "pool_refill_cooldown_sec": 1800}
        gate = RefillGate(cfg)
        self.assertTrue(gate.check()[0])
        gate.record()
        allowed, reason = gate.check()
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("cooldown"))

    def test_daily_max_blocks(self) -> None:
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 2, "pool_refill_cooldown_sec": 0}
        gate = RefillGate(cfg)
        gate.record()
        gate.record()
        allowed, reason = gate.check()
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("daily_max 2/2"))

    def test_new_day_resets_daily_count(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        self._write_state(
            {
                "last_refill_date": yesterday,
                "refills_today": 999,
                "last_refill_ts": time.time(),
            }
        )
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 5, "pool_refill_cooldown_sec": 0}
        gate = RefillGate(cfg)
        allowed, reason = gate.check()
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_record_persists_state(self) -> None:
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 5, "pool_refill_cooldown_sec": 1800}
        gate = RefillGate(cfg)
        gate.record()
        self.assertTrue(RefillGate.STATE_FILE.is_file())
        data = json.loads(RefillGate.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["refills_today"], 1)
        self.assertEqual(data["last_refill_date"], datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.assertIn("last_refill_ts", data)

    def test_bad_state_file_treated_as_fresh(self) -> None:
        RefillGate.STATE_FILE.write_text("not json", encoding="utf-8")
        cfg = {"pool_auto_refill": True, "pool_refill_daily_max": 5, "pool_refill_cooldown_sec": 0}
        gate = RefillGate(cfg)
        allowed, _ = gate.check()
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
