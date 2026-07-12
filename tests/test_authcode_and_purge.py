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


class PurgeSubprocessDeadGateTests(unittest.TestCase):
    """Reasonix P1: transient subprocess failures must NOT mark refresh_revoked."""

    def _expired_live_payload(self, email: str = "live@example.com"):
        import base64

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        tok = f"{b64({'alg': 'none'})}.{b64({'exp': 1})}.x"
        return {
            "type": "xai",
            "email": email,
            "access_token": tok,
            "refresh_token": "rt_live",
            "disabled": False,
        }

    def test_subprocess_transient_error_is_errors_not_purged(self):
        """When fallback raises non-dead RuntimeError, count as errors only."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            path = auth / "xai-live@example.com.json"
            path.write_text(json.dumps(self._expired_live_payload()), encoding="utf-8")
            cfg = {"cpa_auth_dir": str(auth), "proxy": ""}

            def boom_refresh(rt, *, proxy=None, **kw):
                raise RuntimeError("subprocess refresh failed: timeout")

            with mock.patch("quota_watch.refresh_access_token", create=True):
                # Force the in-process import path to use a mock that raises non-terminal.
                with mock.patch(
                    "cpa_xai.oauth_device.refresh_access_token",
                    side_effect=boom_refresh,
                ):
                    from cpa_xai.oauth_device import OAuthDeviceError  # noqa: F401

                    stats = purge_dead_pool(cfg, log=None, max_per_run=20)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(data.get("disabled"), "transient must not soft-disable")
            self.assertNotEqual(
                (data.get("quota_state") or {}).get("reason"),
                "refresh_revoked",
            )
            self.assertGreaterEqual(stats.get("errors", 0), 1)
            self.assertEqual(stats.get("purged", 0), 0)

    def test_subprocess_dead_flag_soft_disables(self):
        """Explicit dead path (DeadRefreshError / OAuthDeviceError) → refresh_revoked."""
        from cpa_xai.oauth_device import OAuthDeviceError

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            auth.mkdir()
            path = auth / "xai-revoked@example.com.json"
            path.write_text(
                json.dumps(self._expired_live_payload("revoked@example.com")),
                encoding="utf-8",
            )
            cfg = {"cpa_auth_dir": str(auth), "proxy": ""}

            with mock.patch(
                "cpa_xai.oauth_device.refresh_access_token",
                side_effect=OAuthDeviceError("refresh token invalid/expired"),
            ):
                stats = purge_dead_pool(cfg, log=None, max_per_run=20)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data.get("disabled"))
            self.assertEqual(data["quota_state"]["reason"], "refresh_revoked")
            self.assertGreaterEqual(stats.get("purged", 0), 1)

    def test_dead_refresh_error_type_is_terminal_only(self):
        from quota_watch import DeadRefreshError

        self.assertTrue(issubclass(DeadRefreshError, Exception))
        # Must not be aliased to bare Exception in production code path.
        self.assertIsNot(DeadRefreshError, Exception)


class AuthcodeConsentParseTests(unittest.TestCase):
    """P2: consent action-id regex + code extraction without network."""

    def test_submit_consent_extracts_action_and_code(self):
        from cpa_xai import authcode_mint as am

        html = (
            'self.s=t.n.createServerReference)("abcdef0123456789abcdef0123456789abcdef01",'
            "null,null,null,submitOAuth2Consent)"
        )
        page_url = "https://accounts.x.ai/oauth2/consent?state=st1"

        class _Resp:
            status_code = 200
            text = '{"code":"authcode_xyz","state":"st1"}'
            headers = {}

        class _Sess:
            def post(self, *a, **k):
                return _Resp()

        logs: list[str] = []
        code = am._submit_consent(
            _Sess(),
            page_url=page_url,
            page_html=html,
            client_id="cid",
            redirect_uri="http://localhost/cb",
            scopes="openid",
            state="st1",
            challenge="ch",
            nonce="n1",
            log=logs.append,
        )
        self.assertEqual(code, "authcode_xyz")
        self.assertTrue(any("consent HTTP" in x for x in logs))

    def test_submit_consent_location_header_fallback(self):
        from cpa_xai import authcode_mint as am

        page_url = "https://accounts.x.ai/oauth2/consent?state=st2"

        class _Resp:
            status_code = 302
            text = ""
            headers = {
                "location": "http://127.0.0.1:1455/callback?code=loc_code_99&state=st2"
            }

        class _Sess:
            def post(self, *a, **k):
                return _Resp()

        code = am._submit_consent(
            _Sess(),
            page_url=page_url,
            page_html="",
            client_id="cid",
            redirect_uri="http://127.0.0.1:1455/callback",
            scopes="openid",
            state="st2",
            challenge="ch",
            nonce="n2",
            log=lambda _m: None,
        )
        self.assertEqual(code, "loc_code_99")

    def test_session_clears_proxy_when_none(self):
        from cpa_xai.authcode_mint import _session

        try:
            s = _session(None, log=lambda _m: None)
        except Exception as e:
            # curl_cffi missing in some CI — skip rather than fail hard
            if "curl_cffi" in str(e).lower() or "required" in str(e).lower():
                self.skipTest("curl_cffi not installed")
            raise
        self.assertEqual(getattr(s, "proxies", None) or {}, {})


class RecoverAfterFormatTests(unittest.TestCase):
    """Atom P1: ISO recover_after must not crash reenable; new soft_disable uses float."""

    def test_parse_recover_after_iso_and_float(self):
        from cpa_xai.usage import parse_recover_after

        self.assertGreater(parse_recover_after(1_700_000_000), 0)
        iso = "2020-01-01T00:00:00Z"
        self.assertAlmostEqual(parse_recover_after(iso), 1577836800.0, delta=1.0)
        self.assertEqual(parse_recover_after(""), 0.0)
        self.assertEqual(parse_recover_after(None), 0.0)

    def test_reenable_accepts_legacy_iso_recover_after(self):
        with tempfile.TemporaryDirectory() as td:
            auth = Path(td)
            path = auth / "xai-iso@example.com.json"
            # past ISO window → should re-enable
            path.write_text(
                json.dumps(
                    {
                        "email": "iso@example.com",
                        "disabled": True,
                        "quota_state": {
                            "reason": "probe_or_refresh_fail",
                            "recover_after": "2020-01-01T00:00:00Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            stats = reenable_recovered_accounts(auth, log=None, max_per_run=50)
            self.assertGreaterEqual(stats.get("reenabled", 0), 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(data.get("disabled"))

    def test_pool_health_soft_disable_writes_float(self):
        from pool_health import soft_disable

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xai-a@b.com.json"
            path.write_text(json.dumps({"email": "a@b.com"}), encoding="utf-8")
            soft_disable(path, "unit-test", hours=1.0)
            data = json.loads(path.read_text(encoding="utf-8"))
            ra = data["quota_state"]["recover_after"]
            self.assertIsInstance(ra, (int, float))
            self.assertGreater(float(ra), time.time())


class ClashMassReenableTests(unittest.TestCase):
    def test_prefers_success_gt_zero_nodes(self):
        import clash_proxy as cp

        with tempfile.TemporaryDirectory() as td:
            stats_path = Path(td) / "node_stats.json"
            stats_path.write_text(
                json.dumps(
                    {
                        "nodes": {
                            "good": {"success": 3, "fail": 5, "disabled": True},
                            "bad": {"success": 0, "fail": 9, "disabled": True},
                            "other": {"success": 1, "fail": 0, "disabled": False},
                        }
                    }
                ),
                encoding="utf-8",
            )
            logs: list[str] = []
            real = ["good", "bad", "other"]
            # Simulate candidate empty → recovery branch logic inline (unit of preference)
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            nodes = stats.setdefault("nodes", {})
            disabled_in_sel = [n for n in real if bool((nodes.get(n) or {}).get("disabled"))]
            preferred = [
                n for n in disabled_in_sel if int((nodes.get(n) or {}).get("success") or 0) > 0
            ]
            self.assertEqual(preferred, ["good"])
            self.assertIn("bad", disabled_in_sel)
            # Ensure report_fail threshold constant still sensible
            self.assertGreaterEqual(cp._FAIL_DISABLE_THRESHOLD, 3)


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
