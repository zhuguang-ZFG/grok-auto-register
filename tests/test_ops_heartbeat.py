#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for ops_heartbeat (no network / no real process scan)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ops_heartbeat import (
    build_heartbeat,
    count_live_pool,
    exit_code_for,
    min_live_from_cfg,
)


class CountLivePoolTests(unittest.TestCase):
    def test_counts_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "xai-a@b.com.json").write_text(
                json.dumps({"email": "a@b.com", "disabled": False}), encoding="utf-8"
            )
            (d / "xai-c@d.com.json").write_text(
                json.dumps({"email": "c@d.com", "disabled": True}), encoding="utf-8"
            )
            (d / "noise.txt").write_text("x", encoding="utf-8")
            live, total = count_live_pool(d)
            self.assertEqual(total, 2)
            self.assertEqual(live, 1)


class HeartbeatLogicTests(unittest.TestCase):
    def test_critical_when_register_dead(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            (auth / "xai-a@b.com.json").write_text(
                json.dumps({"disabled": False}), encoding="utf-8"
            )
            cfg = {"cpa_auth_dir": str(auth), "pool_min_live": 1}
            hb = build_heartbeat(
                root=root,
                cfg=cfg,
                proc_rows={
                    "register": [],
                    "quota_watch": [{"Name": "python.exe", "ProcessId": 1}],
                    "cliproxy": [{"Name": "cli-proxy-api.exe", "ProcessId": 2}],
                },
            )
            self.assertEqual(hb["level"], "critical")
            self.assertEqual(exit_code_for(hb["level"]), 2)
            self.assertFalse(hb["ok"])

    def test_warn_low_pool(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            cfg = {"cpa_auth_dir": str(auth), "pool_min_live": 50}
            hb = build_heartbeat(
                root=root,
                cfg=cfg,
                proc_rows={
                    "register": [{"Name": "python.exe"}],
                    "quota_watch": [{"Name": "python.exe"}],
                    "cliproxy": [{"Name": "cli-proxy-api.exe"}],
                },
            )
            self.assertEqual(hb["level"], "warn")
            self.assertEqual(exit_code_for(hb["level"]), 1)
            self.assertEqual(hb["pool_live_est"], 0)

    def test_ok_all_good(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            for i in range(3):
                (auth / f"xai-{i}@x.com.json").write_text(
                    json.dumps({"disabled": False}), encoding="utf-8"
                )
            cfg = {"cpa_auth_dir": str(auth), "pool_min_live": 2}
            hb = build_heartbeat(
                root=root,
                cfg=cfg,
                proc_rows={
                    "register": [{"Name": "python.exe"}],
                    "quota_watch": [{"Name": "python.exe"}],
                    "cliproxy": [{"Name": "cli-proxy-api.exe"}],
                },
            )
            self.assertEqual(hb["level"], "ok")
            self.assertEqual(exit_code_for(hb["level"]), 0)
            self.assertEqual(hb["pool_live_est"], 3)

    def test_min_live_keys(self):
        self.assertEqual(min_live_from_cfg({"pool_min_live": 80}), 80)
        self.assertEqual(min_live_from_cfg({"quota_watch_min_pool": 90}), 90)
        self.assertEqual(min_live_from_cfg({}), 100)


class CountLivePoolEdgeCases(unittest.TestCase):
    def test_nonexistent_dir(self):
        live, total = count_live_pool(Path("/nonexistent/dir/xyz"))
        self.assertEqual((live, total), (0, 0))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            live, total = count_live_pool(Path(td))
            self.assertEqual((live, total), (0, 0))

    def test_malformed_json_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "xai-good@x.com.json").write_text(
                json.dumps({"disabled": False}), encoding="utf-8"
            )
            (d / "xai-bad@x.com.json").write_text("NOT JSON{{{", encoding="utf-8")
            live, total = count_live_pool(d)
            self.assertEqual(total, 2)
            self.assertEqual(live, 1)


class MinLiveEdgeCases(unittest.TestCase):
    def test_negative_value_ignored(self):
        self.assertEqual(min_live_from_cfg({"pool_min_live": -5}), 100)

    def test_zero_value_ignored(self):
        self.assertEqual(min_live_from_cfg({"pool_min_live": 0}), 100)

    def test_non_numeric_string(self):
        self.assertEqual(min_live_from_cfg({"pool_min_live": "abc"}), 100)

    def test_string_number(self):
        self.assertEqual(min_live_from_cfg({"pool_min_live": "42"}), 42)


class AliveFilterTests(unittest.TestCase):
    def test_cmd_exe_only_not_alive(self):
        hb = build_heartbeat(
            root=Path(tempfile.mkdtemp()),
            cfg={"pool_min_live": 0},
            proc_rows={
                "register": [{"Name": "cmd.exe", "ProcessId": 99}],
                "quota_watch": [{"Name": "python.exe"}],
                "cliproxy": [{"Name": "cli-proxy-api.exe"}],
            },
        )
        self.assertFalse(hb["procs"]["register"]["alive"])

    def test_powershell_only_not_alive(self):
        hb = build_heartbeat(
            root=Path(tempfile.mkdtemp()),
            cfg={"pool_min_live": 0},
            proc_rows={
                "register": [{"Name": "powershell.exe", "ProcessId": 10}],
                "quota_watch": [{"Name": "python.exe"}],
                "cliproxy": [{"Name": "cli-proxy-api.exe"}],
            },
        )
        self.assertFalse(hb["procs"]["register"]["alive"])

    def test_both_register_and_cliproxy_dead(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            (auth / "xai-a@b.com.json").write_text(
                json.dumps({"disabled": False}), encoding="utf-8"
            )
            hb = build_heartbeat(
                root=root,
                cfg={"cpa_auth_dir": str(auth), "pool_min_live": 1},
                proc_rows={
                    "register": [],
                    "quota_watch": [{"Name": "python.exe"}],
                    "cliproxy": [],
                },
            )
            self.assertEqual(hb["level"], "critical")
            self.assertIn("register process not running", hb["alerts"])
            self.assertIn("cli-proxy-api not running", hb["alerts"])


if __name__ == "__main__":
    unittest.main()
