#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for mailtm_otp."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import mailtm_otp as mt


class ExtractCodeTests(unittest.TestCase):
    def test_xai(self):
        self.assertEqual(mt._extract_code("", "AB1-CD2 xAI confirmation"), "AB1-CD2")

    def test_numeric(self):
        self.assertEqual(mt._extract_code("verification code: 998877"), "998877")


class TokenHelpers(unittest.TestCase):
    def test_is_mailtm_token(self):
        blob = json.dumps({"provider": "mailtm", "address": "a@b.com", "jwt": "x"})
        self.assertTrue(mt.is_mailtm_token(blob))
        self.assertFalse(mt.is_mailtm_token("{}"))


class BasesTests(unittest.TestCase):
    def test_primary_only(self):
        cfg = {"mailtm_api_base": "https://api.mail.tm"}
        self.assertEqual(mt._bases(cfg), ["https://api.mail.tm"])

    def test_fallbacks_dedup(self):
        cfg = {
            "mailtm_api_base": "https://api.mail.tm",
            "mailtm_fallback_bases": [
                "https://api.mail.tm",
                "https://api.mail.gw",
                "https://api.duckmail.sbs",
            ],
        }
        self.assertEqual(
            mt._bases(cfg),
            ["https://api.mail.tm", "https://api.mail.gw", "https://api.duckmail.sbs"],
        )


class PickDomainTests(unittest.TestCase):
    def test_skips_banned_domains(self):
        cfg = {
            "mailtm_api_base": "https://api.mail.tm",
            "mailtm_banned_domains": ["web-library.net"],
        }
        with mock.patch.object(
            mt, "list_domains", return_value=["web-library.net", "safe.tm"]
        ):
            domain = mt._pick_domain(cfg)
        self.assertEqual(domain, "safe.tm")

    def test_skips_default_banned(self):
        cfg = {"mailtm_api_base": "https://api.mail.tm"}
        with mock.patch.object(
            mt, "list_domains", return_value=["duckmail.sbs", "safe.tm"]
        ):
            domain = mt._pick_domain(cfg)
        self.assertEqual(domain, "safe.tm")


class CreateInboxTests(unittest.TestCase):
    def test_create_ok(self):
        cfg = {"mailtm_api_base": "https://api.mail.tm"}
        acc = mock.Mock(status_code=201, text='{"id":"1"}')
        acc.json.return_value = {"id": "1"}
        tok = mock.Mock(status_code=200, text='{"token":"JWT123"}')
        tok.json.return_value = {"token": "JWT123"}
        sess = mock.Mock(
            post=mock.Mock(side_effect=[acc, tok]),
        )
        with mock.patch.object(mt, "_session", return_value=sess):
            with mock.patch.object(mt, "_pick_domain", return_value="safe.tm"):
                email, blob = mt.create_inbox(cfg)
        self.assertTrue(email.endswith("@safe.tm"))
        self.assertTrue(mt.is_mailtm_token(blob))
        self.assertEqual(json.loads(blob)["jwt"], "JWT123")

    def test_create_fallback_on_primary_failure(self):
        cfg = {
            "mailtm_api_base": "https://api.mail.tm",
            "mailtm_fallback_bases": ["https://api.mail.gw"],
        }

        def pick_side_effect(ccfg):
            base = ccfg.get("mailtm_api_base")
            if base == "https://api.mail.tm":
                raise RuntimeError("primary down")
            return "gw-library.net"

        acc = mock.Mock(status_code=201, text='{"id":"1"}')
        acc.json.return_value = {"id": "1"}
        tok = mock.Mock(status_code=200, text='{"token":"JWT456"}')
        tok.json.return_value = {"token": "JWT456"}
        sess = mock.Mock(post=mock.Mock(side_effect=[acc, tok]))

        with mock.patch.object(mt, "_session", return_value=sess):
            with mock.patch.object(mt, "_pick_domain", side_effect=pick_side_effect):
                email, blob = mt.create_inbox(cfg)

        self.assertTrue(email.endswith("@gw-library.net"))
        data = json.loads(blob)
        self.assertEqual(data["base"], "https://api.mail.gw")
        self.assertEqual(data["jwt"], "JWT456")


class WaitCodeTests(unittest.TestCase):
    def test_wait_from_list_intro(self):
        token = json.dumps(
            {
                "provider": "mailtm",
                "address": "a@web-library.net",
                "password": "p",
                "jwt": "J",
                "base": "https://api.mail.tm",
            }
        )
        listing = {
            "hydra:member": [
                {
                    "id": "msg1",
                    "subject": "Verify",
                    "intro": "Your verification code: 112233",
                }
            ]
        }
        list_resp = mock.Mock(status_code=200, text=json.dumps(listing))
        list_resp.json.return_value = listing
        detail = {
            "id": "msg1",
            "subject": "Verify",
            "text": ["Your verification code: 112233"],
        }
        detail_resp = mock.Mock(status_code=200, text=json.dumps(detail))
        detail_resp.json.return_value = detail
        sess = mock.Mock(get=mock.Mock(side_effect=[list_resp, detail_resp]))
        with mock.patch.object(mt, "_session", return_value=sess):
            code = mt.wait_code(token, cfg={}, timeout=5, poll_interval=0.1)
        self.assertEqual(code, "112233")


if __name__ == "__main__":
    unittest.main()
