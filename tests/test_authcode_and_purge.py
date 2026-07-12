#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for authcode mint guards and purge terminal-skip (sticky-safe)."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cpa_xai.authcode_mint import mint_with_sso_authcode
from cpa_xai.protocol_mint import ProtocolMintError
from cpa_xai.usage import reenable_recovered_accounts
from quota_watch import purge_dead_pool


class AuthcodeMintGuardTests(unittest.TestCase):
    def test_empty_sso_raises(self):
        with self.assertRaises(ProtocolMintError) as ctx:
            mint_with_sso_authcode(sso_cookie="")
        self.assertIn("missing sso", str(ctx.exception).lower())

    def test_whitespace_sso_raises(self):
        with self.assertRaises(ProtocolMintError):
            mint_with_sso_authcode(sso_cookie="   ")


class PurgeTerminalSkipTests(unittest.TestCase):
    def _expired_payload(self, **extra):
        # JWT-like stub: exp in the past (epoch 1)
        # header.payload.sig — payload is {"exp":1}
        import base64

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        tok = f"{b64({'alg':'none'})}.{b64({'exp': 1})}.x"
        p = {
            "type": "xai",
            "email": "dead@example.com",
            "access_token": tok,
            "refresh_token": "rt_dead",
            "disabled": True,
            "quota_state": {
                "reason": "refresh_revoked",
                "recover_after": time.time() + 3600,
            },
        }
        p.update(extra)
        return p

    def test_purge_skips_refresh_revoked_without_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            path = auth / "xai-dead@example.com.json"
            payload = self._expired_payload()
            path.write_text(json.dumps(payload), encoding="utf-8")
            mtime_before = path.stat().st_mtime
            time.sleep(0.05)
            cfg = {"cpa_auth_dir": str(auth), "proxy": ""}
            stats = purge_dead_pool(cfg, log=None, max_per_run=20)
            self.assertGreaterEqual(stats.get("skipped_terminal", 0), 1)
            self.assertEqual(stats.get("scanned", 0), 0)
            # file not rewritten
            self.assertEqual(path.stat().st_mtime, mtime_before)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data.get("disabled"))
            self.assertEqual(data["quota_state"]["reason"], "refresh_revoked")

    def test_reenable_skips_terminal_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            auth = Path(td)
            path = auth / "xai-dead@example.com.json"
            path.write_text(
                json.dumps(
                    {
                        "email": "dead@example.com",
                        "disabled": True,
                        "quota_state": {
                            "reason": "refresh_revoked",
                            "recover_after": time.time() - 10,  # past, but terminal
                        },
                    }
                ),
                encoding="utf-8",
            )
            stats = reenable_recovered_accounts(auth, log=None, max_per_run=50)
            self.assertGreaterEqual(stats.get("skipped_terminal", 0), 1)
            self.assertEqual(stats.get("reenabled", 0), 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data.get("disabled"))


class MintLogStatsTests(unittest.TestCase):
    def test_authcode_counters(self):
        from pool_status import _mint_log_stats, _cliproxy_affinity_stats

        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "register_auto.out.log"
            log.write_text(
                "\n".join(
                    [
                        "===== RESTART =====",
                        "mint start: a@b.com",
                        "protocol mint ok: a@b.com",
                        "authcode mint ok: c@d.com",
                        "authcode mint failed attempt=1",
                        "export ok method=authcode path=x",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            st = _mint_log_stats(log)
            self.assertEqual(st["protocol_ok"], 1)
            self.assertEqual(st["authcode_ok"], 1)
            self.assertEqual(st["authcode_fail"], 1)
            # counter sums two log patterns that both match this line
            self.assertGreaterEqual(st["export_ok_authcode"], 1)

            clog = Path(td) / "main.log"
            clog.write_text(
                "\n".join(
                    [
                        "session-affinity: cache hit | session=s auth=a.json",
                        "session-affinity: cache miss, new binding",
                        "session-affinity: cache hit but auth unavailable, reselected | session=s",
                        "auth file changed (REMOVE): x.json",
                        "auth file changed (WRITE): y.json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            aff = _cliproxy_affinity_stats(clog)
            self.assertEqual(aff["affinity_hit"], 1)
            self.assertEqual(aff["affinity_miss"], 1)
            self.assertEqual(aff["affinity_reselect"], 1)
            self.assertEqual(aff["auth_remove"], 1)
            self.assertAlmostEqual(aff["reselect_rate"], 1 / 3, places=4)


if __name__ == "__main__":
    unittest.main()
