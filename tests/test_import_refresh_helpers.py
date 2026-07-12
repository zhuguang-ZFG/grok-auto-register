#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import tempfile
import time
import unittest
from pathlib import Path

import import_cpa_batch as imp


class ImportHelpers(unittest.TestCase):
    def test_normalize_and_import_json(self):
        # minimal fake jwt-like not needed — empty exp allowed with keep path
        # build a non-expired-looking token is hard; test normalize only
        d = {
            "type": "xai",
            "email": "a@example.com",
            "access_token": "x.y.z",
            "refresh_token": "rt",
        }
        n = imp.normalize_payload(d, exp=0)
        self.assertEqual(n["email"], "a@example.com")
        self.assertIn("headers", n)
        self.assertFalse(n["disabled"])

    def test_import_paths_dedupe(self):
        with tempfile.TemporaryDirectory() as td:
            auth = Path(td) / "cpa_auths"
            auth.mkdir()
            src = Path(td) / "src"
            src.mkdir()
            # exp far future via field only — jwt_exp will be 0 so not skipped
            payload = {
                "type": "xai",
                "email": "u1@test.com",
                "access_token": "aaa.bbb.ccc",
                "refresh_token": "rt1",
                "sub": "sub-1",
            }
            (src / "1.json").write_text(json.dumps(payload), encoding="utf-8")
            (src / "2.json").write_text(json.dumps(payload), encoding="utf-8")
            r = imp.import_paths([src], auth_dir=auth, keep_expired=True)
            self.assertEqual(r["imported"], 1)
            self.assertEqual(r["stats"].get("dup", 0) + r["stats"].get("dup_file", 0), 1)


if __name__ == "__main__":
    unittest.main()
