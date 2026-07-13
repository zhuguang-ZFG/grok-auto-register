#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for cloud_mail_otp (vip0 / maillab Cloud Mail)."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

import cloud_mail_otp as cm


class ExtractCodeTests(unittest.TestCase):
    def test_xai_subject(self):
        self.assertEqual(cm._extract_code("", "ABC-123 xAI login"), "ABC-123")

    def test_xai_body(self):
        self.assertEqual(cm._extract_code("Your code is XYZ-789 for Grok"), "XYZ-789")

    def test_numeric(self):
        self.assertEqual(cm._extract_code("verification code: 123456"), "123456")


class TokenHelpers(unittest.TestCase):
    def test_is_cloud_mail_token(self):
        blob = json.dumps(
            {"provider": "cloud_mail", "jwt": "x", "accountId": 1, "base": "https://vip0.xyz"}
        )
        self.assertTrue(cm.is_cloud_mail_token(blob))
        self.assertFalse(cm.is_cloud_mail_token("not-json"))
        self.assertFalse(cm.is_cloud_mail_token(json.dumps({"provider": "hotmail"})))


class DomainPickTests(unittest.TestCase):
    def test_normalize_list(self):
        self.assertEqual(
            cm._normalize_domain_list("@vip0.xyz, vip9.cyou; sismi6.bond"),
            ["vip0.xyz", "vip9.cyou", "sismi6.bond"],
        )

    def test_list_available_intersects_server(self):
        cfg = {"cloud_mail_domains": ["vip0.xyz", "evil.example", "vip9.cyou"]}
        with mock.patch.object(
            cm,
            "_session",
            return_value=mock.Mock(
                get=mock.Mock(
                    return_value=mock.Mock(
                        json=mock.Mock(
                            return_value={
                                "data": {
                                    "domainList": ["@vip0.xyz", "@vip9.cyou", "@sismi6.bond"]
                                }
                            }
                        )
                    )
                )
            ),
        ):
            got = cm.list_available_domains(
                cfg, jwt="t", cred={"base": "https://vip0.xyz"}
            )
        self.assertEqual(got, ["vip0.xyz", "vip9.cyou"])

    def test_pick_first_mode(self):
        cfg = {
            "cloud_mail_domains": ["vip0.xyz", "vip9.cyou"],
            "cloud_mail_domain_mode": "first",
        }
        with mock.patch.object(
            cm, "list_available_domains", return_value=["vip0.xyz", "vip9.cyou"]
        ):
            self.assertEqual(cm._pick_domain(cfg, {}, "jwt"), "vip0.xyz")

    def test_reuse_turnstile_across_domains(self):
        """One CapSolver solve, two domain tries if first fails."""
        login_resp = mock.Mock(status_code=200, text='{"code":200,"data":{"token":"JWT"}}')
        login_resp.json.return_value = {"code": 200, "data": {"token": "JWT"}}
        fail_resp = mock.Mock(status_code=200, text='{"code":400,"message":"domain blocked"}')
        fail_resp.json.return_value = {"code": 400, "message": "domain blocked"}
        ok_resp = mock.Mock(
            status_code=200,
            text='{"code":200,"data":{"accountId":7,"email":"u@vip0.xyz"}}',
        )
        ok_resp.json.return_value = {
            "code": 200,
            "data": {"accountId": 7, "email": "u@vip0.xyz"},
        }
        sess = mock.Mock()
        sess.post.side_effect = [login_resp, fail_resp, ok_resp]
        solves = {"n": 0}

        def fake_solve(*_a, **_k):
            solves["n"] += 1
            return "T" * 100

        with mock.patch.object(cm, "_session", return_value=sess):
            with mock.patch.object(cm, "_solve_turnstile", side_effect=fake_solve):
                with mock.patch.object(
                    cm,
                    "_load_credentials",
                    return_value={
                        "base": "https://vip0.xyz",
                        "email": "m@vip0.xyz",
                        "password": "pw",
                        "sitekey": "0xK",
                    },
                ):
                    with mock.patch.object(
                        cm,
                        "_pick_domain_candidates",
                        return_value=["bad.example", "vip0.xyz"],
                    ):
                        with mock.patch.object(cm, "record_domain_fail"):
                            with mock.patch.object(cm, "record_domain_ok"):
                                email, tok = cm.create_inbox({"cloud_mail_turnstile_max": 2})
        self.assertEqual(solves["n"], 1)
        self.assertTrue(email.endswith("@vip0.xyz"))
        self.assertEqual(json.loads(tok)["turnstile_solves"], 1)

    def test_domain_cooldown_zeros_weight(self):
        state = {
            "domains": {
                "dead.example": {
                    "ok": 0,
                    "fail": 5,
                    "streak_fail": 5,
                    "cooldown_until": time.time() + 9999,
                },
                "vip0.xyz": {"ok": 3, "fail": 0, "streak_fail": 0, "cooldown_until": 0},
            }
        }
        now = time.time()
        self.assertEqual(cm._domain_weight("dead.example", state, now), 0.0)
        self.assertGreater(cm._domain_weight("vip0.xyz", state, now), 1.0)


class WaitCodeTests(unittest.TestCase):
    def test_wait_code_from_list(self):
        blob = json.dumps(
            {
                "provider": "cloud_mail",
                "jwt": "tok",
                "accountId": 11,
                "base": "https://vip0.xyz",
                "email": "tmp@vip0.xyz",
                "master": "master@vip0.xyz",
            }
        )
        msgs = [
            {
                "emailId": 9,
                "subject": "ABC-DEF xAI",
                "text": "welcome",
                "content": "",
                "createTime": "t1",
            }
        ]
        with mock.patch.object(cm, "list_messages", return_value=msgs):
            with mock.patch.object(cm, "delete_account", return_value=True) as d:
                code = cm.wait_code(
                    blob,
                    "tmp@vip0.xyz",
                    cfg={"cloud_mail_delete_after_code": True},
                    timeout=10,
                    poll_interval=0.01,
                )
        self.assertEqual(code.upper(), "ABC-DEF")
        d.assert_called_once()

    def test_wait_code_uses_server_code_field(self):
        blob = json.dumps(
            {"provider": "cloud_mail", "jwt": "t", "accountId": 1, "base": "https://x"}
        )
        msgs = [{"emailId": 1, "subject": "hi", "code": "112233", "text": "", "content": ""}]
        with mock.patch.object(cm, "list_messages", return_value=msgs):
            with mock.patch.object(cm, "delete_account", return_value=True):
                code = cm.wait_code(
                    blob,
                    cfg={"cloud_mail_delete_after_code": True},
                    timeout=5,
                    poll_interval=0.01,
                )
        self.assertEqual(code, "112233")

    def test_wait_code_can_skip_delete(self):
        blob = json.dumps(
            {"provider": "cloud_mail", "jwt": "t", "accountId": 1, "base": "https://x"}
        )
        msgs = [{"emailId": 1, "subject": "ABC-DEF xAI", "text": "", "content": ""}]
        with mock.patch.object(cm, "list_messages", return_value=msgs):
            with mock.patch.object(cm, "delete_account") as d:
                code = cm.wait_code(
                    blob,
                    cfg={"cloud_mail_delete_after_code": False},
                    timeout=5,
                    poll_interval=0.01,
                )
        self.assertEqual(code.upper(), "ABC-DEF")
        d.assert_not_called()


class CreateInboxMockTests(unittest.TestCase):
    def test_create_inbox_flow(self):
        login_resp = mock.Mock(status_code=200)
        login_resp.text = '{"code":200,"data":{"token":"JWT123"}}'
        login_resp.json.return_value = {"code": 200, "data": {"token": "JWT123"}}

        add_resp = mock.Mock(status_code=200)
        add_resp.text = (
            '{"code":200,"data":{"accountId":99,"email":"tmpabc@vip0.xyz"}}'
        )
        add_resp.json.return_value = {
            "code": 200,
            "data": {"accountId": 99, "email": "tmpabc@vip0.xyz"},
        }

        sess = mock.Mock()
        sess.post.side_effect = [login_resp, add_resp]
        sess.get.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(
                return_value={
                    "code": 200,
                    "data": {"domainList": ["@vip0.xyz"], "siteKey": "0xKEY"},
                }
            ),
        )

        with mock.patch.object(cm, "_session", return_value=sess):
            with mock.patch.object(cm, "_solve_turnstile", return_value="T" * 100):
                with mock.patch.object(
                    cm,
                    "_load_credentials",
                    return_value={
                        "base": "https://vip0.xyz",
                        "email": "master@vip0.xyz",
                        "password": "pw",
                        "sitekey": "0xKEY",
                        "domain": "vip0.xyz",
                    },
                ):
                    email, tok = cm.create_inbox({"capsolver_api_key": "k"})
        self.assertTrue(email.endswith("@vip0.xyz"))
        data = json.loads(tok)
        self.assertEqual(data["provider"], "cloud_mail")
        self.assertEqual(data["accountId"], 99)
        self.assertEqual(data["jwt"], "JWT123")


if __name__ == "__main__":
    unittest.main()
