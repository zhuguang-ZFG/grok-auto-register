#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hotmail_pool as hp


class ParsePopTests(unittest.TestCase):
    def test_parse_line(self):
        row = hp.parse_line(
            "a@hotmail.com----pass123----9e5f94bc-e8a4-4e73-b8be-63364c29d753----M.C5_refresh"
        )
        self.assertEqual(row["email"], "a@hotmail.com")
        self.assertEqual(row["password"], "pass123")
        self.assertTrue(row["refresh_token"].startswith("M.C5"))

    def test_pop_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool = root / "pool.txt"
            used = root / "used.txt"
            pool.write_text(
                "# c\n"
                "a@hotmail.com----p----uuid----rt1\n"
                "b@hotmail.com----p----uuid----rt2\n",
                encoding="utf-8",
            )
            cfg = {
                "hotmail_pool_path": str(pool),
                "hotmail_pool_used_path": str(used),
            }
            row = hp.pop_account(cfg)
            self.assertEqual(row["email"], "a@hotmail.com")
            left = hp.load_pool(pool)
            self.assertEqual(len(left), 1)
            self.assertEqual(left[0]["email"], "b@hotmail.com")
            self.assertTrue(used.is_file())


class ExtractCodeTests(unittest.TestCase):
    def test_xai_subject(self):
        self.assertEqual(hp._extract_code("", "ABC-123 xAI"), "ABC-123")

    def test_xai_confirmation_subject(self):
        self.assertEqual(
            hp._extract_code("", "DHB-Z5V xAI confirmation code"),
            "DHB-Z5V",
        )

    def test_digit_code_requires_xai_brand(self):
        # generic "Verify" alone must not yield a code (OpenAI-style false positive)
        self.assertIsNone(
            hp._extract_code("Your verification code: 482910", "Verify")
        )
        self.assertEqual(
            hp._extract_code("Your verification code: 482910", "xAI verify"),
            "482910",
        )

    def test_rejects_openai_sign_in_code(self):
        # shared hotmail inbox often mixes OpenAI OTP; must not steal it
        self.assertIsNone(
            hp._extract_code(
                "2BL-2BF is your code",
                "New sign-in to your OpenAI account",
                from_addr="noreply@openai.com",
            )
        )

    def test_rejects_maxai_substring_brand(self):
        # bare "xai" must not match inside "maxai"
        self.assertFalse(
            hp._looks_like_xai_mail(
                "Welcome from maxai",
                "your code is AB1-CD2 in body",
                from_addr="hi@maxai.com",
            )
        )
        self.assertIsNone(
            hp._extract_code(
                "your code is AB1-CD2 in body",
                "Welcome from maxai",
                from_addr="hi@maxai.com",
            )
        )

    def test_accepts_xai_sender_domain(self):
        self.assertTrue(
            hp._looks_like_xai_mail(
                "Confirm your email",
                "Your verification code: 482910",
                from_addr="noreply@x.ai",
            )
        )
        self.assertEqual(
            hp._extract_code(
                "Your verification code: 482910",
                "Confirm your email",
                from_addr="noreply@x.ai",
            ),
            "482910",
        )

    def test_ignores_outlook_welcome_app_img(self):
        html = '<img class="app-img" src="https://cdn.example/app-img.png">'
        self.assertIsNone(
            hp._extract_code(html, "Welcome to your new Outlook.com account")
        )


class WaitCodeTests(unittest.TestCase):
    def test_wait_code_from_imap(self):
        row = {
            "email": "a@hotmail.com",
            "refresh_token": "rt",
            "client_id": "cid",
        }
        with mock.patch.object(hp, "refresh_access_token", return_value="atok"):
            with mock.patch.object(
                hp,
                "imap_fetch_recent",
                return_value=[
                    {"subject": "QI2-VY8 xAI", "text": "code", "id": "1", "from": "x"}
                ],
            ):
                code = hp.wait_code(
                    json.dumps(row),
                    "a@hotmail.com",
                    cfg={},
                    timeout=10,
                    poll_interval=0.01,
                )
        self.assertEqual(code.upper(), "QI2-VY8")


if __name__ == "__main__":
    unittest.main()
