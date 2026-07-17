#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for cpa_xai.quarantine."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cpa_xai.quarantine import (
    DEFAULT_RECOVER_AFTER_SEC,
    discard_auth,
    iter_quarantined,
    move_to_live,
    quarantine_auth,
    update_hold,
)


class QuarantineTests(unittest.TestCase):
    def test_quarantine_and_iter_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = {"access_token": "at", "refresh_token": "rt", "email": "a@b.com"}
            p = quarantine_auth(auth, root=root, reason="permission_denied", recover_after_sec=0)
            self.assertTrue(p.exists())
            ready = list(iter_quarantined(root))
            self.assertEqual(len(ready), 1)

    def test_move_to_live(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = {"access_token": "at", "refresh_token": "rt", "email": "a@b.com"}
            p = quarantine_auth(auth, root=root, reason="permission_denied", recover_after_sec=0)
            live = move_to_live(p, auth, root=root)
            self.assertTrue(live.exists())
            self.assertFalse(p.exists())
            self.assertEqual(live.parent.name, "cpa_auths")

    def test_update_hold_extends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = {"access_token": "at", "refresh_token": "rt", "email": "a@b.com"}
            p = quarantine_auth(auth, root=root, reason="permission_denied", recover_after_sec=1)
            data = json.loads(p.read_text(encoding="utf-8"))
            old_hold = data["_quarantine"]["hold_until_ts"]
            update_hold(p, data, recover_after_sec=3600, new_status="still_denied")
            self.assertEqual(data["_quarantine"]["retest_count"], 1)
            self.assertGreater(data["_quarantine"]["hold_until_ts"], old_hold)

    def test_discard_auth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = {"access_token": "at", "refresh_token": "rt", "email": "a@b.com"}
            p = discard_auth(auth, root=root, reason="unauthorized")
            self.assertTrue(p.exists())
            self.assertIn("_discarded", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
