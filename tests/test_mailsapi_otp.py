#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for mailsapi fixed-OTP backup channel."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mailsapi_otp as mo


class ParseAndLoadTests(unittest.TestCase):
    def test_parse_line_dashes(self):
        got = mo.parse_credential_line(
            "a@b.com----https://gapi.mailsapi.com/api/get-code?uid=x"
        )
        self.assertEqual(got[0], "a@b.com")
        self.assertTrue(got[1].startswith("http"))

    def test_parse_skips_comment(self):
        self.assertIsNone(mo.parse_credential_line("# x----http://y"))

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "mail_credentials.txt").write_text(
                "u@g.com----https://example.com/api/get-code?uid=1\n",
                encoding="utf-8",
            )
            entries = mo.load_entries({}, root=root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["email"], "u@g.com")

    def test_pick_inbox_prefers_config_email(self):
        cfg = {
            "mailsapi_entries": [
                {"email": "a@x.com", "url": "https://example.com/a"},
                {"email": "b@x.com", "url": "https://example.com/b"},
            ],
            "mailsapi_email": "b@x.com",
        }
        email, url = mo.pick_inbox(cfg, root=Path("."))
        self.assertEqual(email, "b@x.com")
        self.assertEqual(url, "https://example.com/b")


class FetchCodeTests(unittest.TestCase):
    def test_fetch_code_parses_data_code(self):
        class R:
            status_code = 200

            def json(self):
                return {"code": 0, "message": "SUCCESS", "data": {"code": "080782"}}

        with mock.patch("requests.get", return_value=R()):
            code = mo.fetch_code("https://gapi.mailsapi.com/api/get-code?uid=x")
        self.assertEqual(code, "080782")

    def test_wait_code_waits_for_change(self):
        seq = [mock.Mock(return_value="111111"), mock.Mock(return_value="111111"), mock.Mock(return_value="222222")]
        with mock.patch.object(mo, "fetch_code", side_effect=[s.return_value for s in seq]):
            with mock.patch.object(mo.time, "sleep", return_value=None):
                code = mo.wait_code(
                    "https://example.com/get-code",
                    "a@b.com",
                    cfg={"mailsapi_accept_cached_code": False},
                    timeout=30,
                    poll_interval=0.01,
                )
        self.assertEqual(code, "222222")

    def test_wait_code_accept_cached(self):
        with mock.patch.object(mo, "fetch_code", return_value="080782"):
            code = mo.wait_code(
                "https://example.com/get-code",
                "a@b.com",
                cfg={"mailsapi_accept_cached_code": True},
                timeout=5,
                poll_interval=0.01,
            )
        self.assertEqual(code, "080782")


if __name__ == "__main__":
    unittest.main()
