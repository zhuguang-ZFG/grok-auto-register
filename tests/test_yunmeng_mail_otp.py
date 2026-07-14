#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for yunmeng_mail_otp (云梦无限邮箱)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import yunmeng_mail_otp as ym


class ExtractCodeTests(unittest.TestCase):
    def test_xai_subject(self):
        self.assertEqual(ym._extract_code("", "ABC-123 xAI login"), "ABC-123")

    def test_xai_body(self):
        self.assertEqual(ym._extract_code("Your code is XYZ-789 for Grok"), "XYZ-789")

    def test_numeric(self):
        self.assertEqual(ym._extract_code("verification code: 123456"), "123456")


class TokenHelpers(unittest.TestCase):
    def test_is_yunmeng_token(self):
        blob = json.dumps(
            {
                "provider": "yunmeng",
                "mailbox": "a@mail.jijiu6.xyz",
                "base": "https://ym-mail.ymmynb.com",
            }
        )
        self.assertTrue(ym.is_yunmeng_token(blob))
        self.assertFalse(ym.is_yunmeng_token("not-json"))
        self.assertFalse(ym.is_yunmeng_token(json.dumps({"provider": "hotmail"})))


class DomainPickTests(unittest.TestCase):
    def test_normalize_list(self):
        self.assertEqual(
            ym._normalize_domain_list("@mail.jijiu6.xyz, xarg.xyz; mail.hitodev.com"),
            ["mail.jijiu6.xyz", "xarg.xyz", "mail.hitodev.com"],
        )

    def test_pick_prefers_configured(self):
        cfg = {"yunmeng_domain": "xarg.xyz"}
        with mock.patch.object(
            ym, "list_domains", return_value=(["mail.jijiu6.xyz", "xarg.xyz"], "mail.jijiu6.xyz")
        ):
            self.assertEqual(ym._pick_domain(cfg), "xarg.xyz")


class CreateInboxTests(unittest.TestCase):
    def test_create_inbox_ok(self):
        cfg = {"yunmeng_base": "https://ym-mail.ymmynb.com", "yunmeng_domain": "mail.jijiu6.xyz"}
        mb = {
            "success": True,
            "data": {
                "mailbox": {
                    "id": "abc",
                    "prefix": "utest123",
                    "domain": "mail.jijiu6.xyz",
                    "fullAddress": "utest123@mail.jijiu6.xyz",
                }
            },
        }
        resp = mock.Mock(status_code=201, text=json.dumps(mb))
        resp.json.return_value = mb
        sess = mock.Mock(post=mock.Mock(return_value=resp))
        with mock.patch.object(ym, "_session", return_value=sess):
            with mock.patch.object(ym, "_pick_domain", return_value="mail.jijiu6.xyz"):
                email, tok = ym.create_inbox(cfg)
        self.assertEqual(email, "utest123@mail.jijiu6.xyz")
        self.assertTrue(ym.is_yunmeng_token(tok))
        obj = json.loads(tok)
        self.assertEqual(obj["mailbox"], email)
        self.assertEqual(obj["provider"], "yunmeng")


class WaitCodeTests(unittest.TestCase):
    def test_wait_code_finds_body(self):
        token = json.dumps(
            {
                "provider": "yunmeng",
                "mailbox": "a@mail.jijiu6.xyz",
                "base": "https://ym-mail.ymmynb.com",
                "api_version": "1.4",
            }
        )
        payload = {
            "success": True,
            "data": {
                "emails": [
                    {
                        "_id": "1",
                        "from": "noreply@x.ai",
                        "subject": "Verify",
                        "text": "Your verification code: 654321",
                        "receivedAt": "2026-07-14T00:00:00Z",
                    }
                ]
            },
        }
        resp = mock.Mock(status_code=200, text=json.dumps(payload))
        resp.json.return_value = payload
        sess = mock.Mock(get=mock.Mock(return_value=resp))
        with mock.patch.object(ym, "_session", return_value=sess):
            code = ym.wait_code(token, "a@mail.jijiu6.xyz", cfg={}, timeout=5, poll_interval=0.1)
        self.assertEqual(code, "654321")


if __name__ == "__main__":
    unittest.main()
