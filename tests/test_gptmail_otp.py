#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for gptmail_otp."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import gptmail_otp as gm


class ExtractCodeTests(unittest.TestCase):
    def test_xai(self):
        self.assertEqual(gm._extract_code("", "AA1-BB2 xAI confirmation"), "AA1-BB2")

    def test_numeric(self):
        self.assertEqual(gm._extract_code("verification code: 445566"), "445566")


class TokenHelpers(unittest.TestCase):
    def test_is_gptmail_token(self):
        blob = json.dumps({"provider": "gptmail", "email": "a@b.com", "api_key": "k"})
        self.assertTrue(gm.is_gptmail_token(blob))
        self.assertFalse(gm.is_gptmail_token("{}"))


class CreateInboxTests(unittest.TestCase):
    def test_create_ok(self):
        body = {"success": True, "data": {"email": "x@example.com"}, "error": ""}
        resp = mock.Mock(status_code=200, text=json.dumps(body))
        resp.json.return_value = body
        sess = mock.Mock(get=mock.Mock(return_value=resp))
        with mock.patch.object(gm, "_session", return_value=sess):
            email, tok = gm.create_inbox({"gptmail_api_key": "gpt-test"})
        self.assertEqual(email, "x@example.com")
        self.assertTrue(gm.is_gptmail_token(tok))

    def test_create_invalid_key(self):
        body = {"success": False, "error": "Invalid API key"}
        resp = mock.Mock(status_code=401, text=json.dumps(body))
        resp.json.return_value = body
        sess = mock.Mock(get=mock.Mock(return_value=resp), post=mock.Mock(return_value=resp))
        with mock.patch.object(gm, "_session", return_value=sess):
            with self.assertRaises(RuntimeError):
                gm.create_inbox({"gptmail_api_key": "bad"})


class WaitCodeTests(unittest.TestCase):
    def test_wait_code(self):
        token = json.dumps(
            {
                "provider": "gptmail",
                "email": "a@example.com",
                "base": "https://mail.chatgpt.org.uk",
                "api_key": "k",
            }
        )
        listing = {
            "success": True,
            "data": {
                "emails": [
                    {
                        "id": "1",
                        "subject": "Verify",
                        "content": "Your verification code: 778899",
                    }
                ],
                "count": 1,
            },
        }
        resp = mock.Mock(status_code=200, text=json.dumps(listing))
        resp.json.return_value = listing
        sess = mock.Mock(get=mock.Mock(return_value=resp))
        with mock.patch.object(gm, "_session", return_value=sess):
            code = gm.wait_code(token, cfg={}, timeout=5, poll_interval=0.1)
        self.assertEqual(code, "778899")


if __name__ == "__main__":
    unittest.main()
