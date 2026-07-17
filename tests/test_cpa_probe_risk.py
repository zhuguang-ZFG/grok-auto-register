#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for community-absorbed probe risk helpers."""
from __future__ import annotations

import base64
import json
import unittest

from cpa_xai.probe import _chat_error_kind, decode_token_risk
from cpa_xai.schema import DEFAULT_CLIENT_HEADERS


def _jwt(payload: dict) -> str:
    head = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{head}.{body}.sig"


class DecodeTokenRiskTests(unittest.TestCase):
    def test_clean_when_missing_flag(self) -> None:
        tok = _jwt({"sub": "u1", "scope": "openid"})
        r = decode_token_risk(tok)
        self.assertFalse(r["bot_flagged"])
        self.assertIsNone(r["bot_flag_source"])
        self.assertEqual(r["sub"], "u1")

    def test_flagged_when_nonzero(self) -> None:
        tok = _jwt({"sub": "u2", "bot_flag_source": 3, "risk_score": 9})
        r = decode_token_risk(tok)
        self.assertTrue(r["bot_flagged"])
        self.assertEqual(r["bot_flag_source"], 3)
        self.assertIn("bot_flag_source", r["risk_claims"])

    def test_zero_is_clean(self) -> None:
        tok = _jwt({"bot_flag_source": 0})
        r = decode_token_risk(tok)
        self.assertFalse(r["bot_flagged"])


class ChatErrorKindTests(unittest.TestCase):
    def test_permission_denied(self) -> None:
        self.assertEqual(
            _chat_error_kind(403, "Access to the chat endpoint is denied"),
            "permission-denied",
        )

    def test_quota(self) -> None:
        self.assertEqual(
            _chat_error_kind(429, '{"code":"subscription:free-usage-exhausted"}'),
            "quota-exhausted",
        )

    def test_rate_limit_text(self) -> None:
        self.assertEqual(_chat_error_kind(200, "rate limit exceeded"), "rate-limit")


class HeaderFingerprintTests(unittest.TestCase):
    def test_dual_token_auth_and_compaction(self) -> None:
        self.assertEqual(DEFAULT_CLIENT_HEADERS["x-xai-token-auth"], "xai-grok-cli")
        self.assertEqual(DEFAULT_CLIENT_HEADERS["X-XAI-Token-Auth"], "xai-grok-cli")
        self.assertEqual(DEFAULT_CLIENT_HEADERS["x-compaction-at"], "400000")


if __name__ == "__main__":
    unittest.main()
