"""Tests for proactive token refresh + pool expiry handling."""

import json
import time
import base64
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import sys
import os

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import quota_watch as qw
import local_grok_auth as lga


def _fake_jwt(*, exp_in_sec: int, sub: str = "test-sub") -> str:
    """Build a minimal JWT whose exp is now + exp_in_sec. No signature validity needed."""
    now = int(time.time())
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": sub, "iat": now - 100, "exp": now + exp_in_sec
    }).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class PoolExpiryTests(unittest.TestCase):
    def test_pool_token_not_expired(self):
        token = _fake_jwt(exp_in_sec=3600)
        self.assertFalse(qw.pool_token_is_expired({"access_token": token}))

    def test_pool_token_expired(self):
        token = _fake_jwt(exp_in_sec=-10)
        self.assertTrue(qw.pool_token_is_expired({"access_token": token}))

    def test_no_token_not_expired(self):
        # no token -> not expired (delegated to probe), don't false-positive
        self.assertFalse(qw.pool_token_is_expired({}))
        self.assertFalse(qw.pool_token_is_expired({"access_token": ""}))

    def test_list_cpa_pool_drop_expired(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "cpa_auths"
            d.mkdir()
            (d / "xai-alive.json").write_text(json.dumps(
                {"access_token": _fake_jwt(exp_in_sec=3600), "email": "alive@x"}), encoding="utf-8")
            (d / "xai-dead.json").write_text(json.dumps(
                {"access_token": _fake_jwt(exp_in_sec=-100), "email": "dead@x"}), encoding="utf-8")
            cfg = {"cpa_auth_dir": str(d)}
            all_files = qw.list_cpa_pool(cfg)
            self.assertEqual(len(all_files), 2)
            valid = qw.list_cpa_pool(cfg, drop_expired=True)
            self.assertEqual(len(valid), 1)
            self.assertIn("alive", valid[0].name)


class RefreshAuthEntryTests(unittest.TestCase):
    def test_refresh_writes_back_and_preserves_email(self):
        import tempfile
        new_token = _fake_jwt(exp_in_sec=7200, sub="new-sub")
        old_token = _fake_jwt(exp_in_sec=100, sub="old-sub")

        with tempfile.TemporaryDirectory() as td:
            ap = Path(td) / "auth.json"
            # seed auth.json with old entry
            lga.write_local_grok_auth(
                access_token=old_token,
                refresh_token="old-rt",
                email="keepme@x.ai",
                auth_path=ap,
            )

            # mock refresh_access_token to return fresh tokens
            from cpa_xai.oauth_device import TokenResult
            with patch("cpa_xai.oauth_device.refresh_access_token") as mock_ref:
                mock_ref.return_value = TokenResult(
                    access_token=new_token,
                    refresh_token="new-rt",
                    id_token=None,
                    token_type="Bearer",
                    expires_in=7200,
                    raw={},
                )
                r = lga.refresh_auth_entry(ap, log=print)
            self.assertTrue(r.get("ok"))
            # email preserved
            entry = lga.load_auth_file(ap).get(lga.AUTH_ENTRY_KEY)
            self.assertEqual(entry.get("email"), "keepme@x.ai")
            # token updated
            self.assertEqual(entry.get("key") or entry.get("access_token"), new_token)
            self.assertEqual(entry.get("refresh_token"), "new-rt")

    def test_refresh_invalid_does_not_raise(self):
        import tempfile
        from cpa_xai.oauth_device import OAuthDeviceError
        with tempfile.TemporaryDirectory() as td:
            ap = Path(td) / "auth.json"
            lga.write_local_grok_auth(
                access_token=_fake_jwt(exp_in_sec=100),
                refresh_token="dead-rt",
                email="x@y.z",
                auth_path=ap,
            )
            with patch("cpa_xai.oauth_device.refresh_access_token") as mock_ref:
                mock_ref.side_effect = OAuthDeviceError("refresh token invalid/expired: invalid_grant")
                r = lga.refresh_auth_entry(ap, log=print)
            self.assertFalse(r.get("ok"))
            self.assertIn("invalid", str(r.get("reason", "")).lower())

    def test_refresh_no_refresh_token(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ap = Path(td) / "auth.json"
            lga.write_local_grok_auth(
                access_token=_fake_jwt(exp_in_sec=100),
                refresh_token="",  # empty
                email="x@y.z",
                auth_path=ap,
            )
            r = lga.refresh_auth_entry(ap)
            self.assertFalse(r.get("ok"))
            self.assertIn("refresh_token", r.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
