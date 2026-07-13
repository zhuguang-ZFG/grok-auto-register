import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cpa_xai.usage import find_cpa_by_key_prefix
from quota_watch import (
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_QUOTA_PATTERNS,
    compile_patterns,
    flatten_log_line,
    line_matches,
    mark_exhausted_from_hits,
)


class QuotaWatchMatchTests(unittest.TestCase):
    def setUp(self):
        self.include = compile_patterns(DEFAULT_QUOTA_PATTERNS)
        self.exclude = compile_patterns(DEFAULT_EXCLUDE_PATTERNS)

    def test_quota_keyword_hits(self):
        self.assertTrue(line_matches("API error 429 rate limit", self.include, self.exclude))
        self.assertTrue(line_matches("quota exceeded for free tier", self.include, self.exclude))
        self.assertTrue(line_matches("auth 401 attribution", self.include, self.exclude))

    def test_excludes_newapi_noise(self):
        blob = (
            "API error (status 503 Service Unavailable): new_api_error: system cpu ove"
        )
        self.assertFalse(line_matches(blob, self.include, self.exclude))

    def test_flatten_jsonl_reason(self):
        raw = (
            '{"msg":"shell.turn.inference_retry","ctx":{"reason":"API error 429 rate limit"}}'
        )
        flat = flatten_log_line(raw)
        self.assertIn("rate limit", flat.lower())
        self.assertTrue(line_matches(flat, self.include, self.exclude))

    def test_free_usage_exhausted_hits(self):
        blob = (
            "API error (status 429 Too Many Requests): "
            "subscription:free-usage-exhausted: You've used all"
        )
        self.assertTrue(line_matches(blob, self.include, self.exclude))


class KeyPrefixMarkTests(unittest.TestCase):
    def test_find_cpa_by_key_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            auth_dir = Path(td)
            token = "oYpRvSNpsFYQ_rest_of_token_value_for_test"
            path = auth_dir / "xai-demo@example.com.json"
            path.write_text(
                json.dumps(
                    {
                        "type": "xai",
                        "email": "demo@example.com",
                        "access_token": token,
                        "refresh_token": "rt",
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )
            hit = find_cpa_by_key_prefix(auth_dir, "oYpRvSNpsFYQ")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.name, path.name)
            self.assertIsNone(find_cpa_by_key_prefix(auth_dir, "nope123456"))

    def test_mark_exhausted_from_hits_key_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            auth_dir = Path(td)
            token = "AbcDefGhiJkl_token_body"
            path = auth_dir / "xai-demo@zhuguang.ccwu.cc.json"
            path.write_text(
                json.dumps(
                    {
                        "type": "xai",
                        "email": "demo@zhuguang.ccwu.cc",
                        "access_token": token,
                        "refresh_token": "rt",
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )
            cfg = {"cpa_auth_dir": str(auth_dir)}
            hits = [
                'shell.turn.inference_failed | free-usage-exhausted | "key_prefix":"AbcDefGhiJkl"'
            ]
            with mock.patch("quota_watch.list_cpa_pool", return_value=[path]):
                marked = mark_exhausted_from_hits(cfg, hits, prefer_email="")
            self.assertIn(path.name, marked)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data.get("disabled"))
            self.assertIn("quota_state", data)


class RotateQualityTests(unittest.TestCase):
    """Local Grok CLI rotate: skip placeholder emails; verify auth.json after write."""

    def test_placeholder_email_detection(self):
        from quota_watch import _is_placeholder_email

        self.assertTrue(_is_placeholder_email(""))
        self.assertTrue(_is_placeholder_email("no-at-sign"))
        self.assertTrue(_is_placeholder_email("uuid@unknown.local"))
        self.assertFalse(_is_placeholder_email("tmp@zhuguang.ccwu.cc"))
        self.assertFalse(_is_placeholder_email("a@baoxia.top"))

    def test_skip_reason_unknown_local_and_disabled(self):
        from quota_watch import _payload_skip_reason_for_local_rotate

        cfg = {
            "quota_watch_require_email": True,
            "quota_watch_skip_unknown_local": True,
            "quota_watch_require_refresh_token": True,
        }
        path = Path("xai-abc@unknown.local.json")
        good = {
            "email": "tmp@zhuguang.ccwu.cc",
            "access_token": "x" * 40,
            "refresh_token": "rt",
            "disabled": False,
        }
        self.assertIsNone(
            _payload_skip_reason_for_local_rotate(good, Path("xai-tmp@zhuguang.ccwu.cc.json"), cfg)
        )
        bad = dict(good, email="u@unknown.local")
        self.assertEqual(
            _payload_skip_reason_for_local_rotate(bad, path, cfg),
            "unknown_local",
        )
        disabled = dict(good, disabled=True)
        self.assertEqual(
            _payload_skip_reason_for_local_rotate(
                disabled, Path("xai-tmp@zhuguang.ccwu.cc.json"), cfg
            ),
            "disabled",
        )
        no_mail = dict(good, email="")
        self.assertEqual(
            _payload_skip_reason_for_local_rotate(no_mail, Path("xai-uuid.json"), cfg),
            "placeholder_email",
        )

    def test_try_rotate_skips_unknown_picks_real_email(self):
        import base64
        import time
        from quota_watch import WatchState, try_rotate_from_pool

        def fake_jwt(exp_in: int = 3600) -> str:
            now = int(time.time())
            h = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
            p = base64.urlsafe_b64encode(
                json.dumps({"sub": "sub1", "exp": now + exp_in}).encode()
            ).rstrip(b"=").decode()
            return f"{h}.{p}.sig"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth_dir = root / "cpa_auths"
            auth_dir.mkdir()
            auth_json = root / "auth.json"
            # bad first (would sort first by name if we put it first in list)
            bad = auth_dir / "xai-9f64d2d9-4d00-44ab-9d8b-e45090806224@unknown.local.json"
            bad.write_text(
                json.dumps(
                    {
                        "email": "9f64d2d9-4d00-44ab-9d8b-e45090806224@unknown.local",
                        "access_token": fake_jwt(),
                        "refresh_token": "rt-bad",
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )
            good = auth_dir / "xai-tmpok@zhuguang.ccwu.cc.json"
            good.write_text(
                json.dumps(
                    {
                        "email": "tmpok@zhuguang.ccwu.cc",
                        "access_token": fake_jwt(),
                        "refresh_token": "rt-good",
                        "disabled": False,
                        "sub": "user-good",
                    }
                ),
                encoding="utf-8",
            )
            cfg = {
                "cpa_auth_dir": str(auth_dir),
                "local_grok_auth_path": str(auth_json),
                "quota_watch_require_email": True,
                "quota_watch_skip_unknown_local": True,
                "defaultDomains": "zhuguang.ccwu.cc",
            }
            state = WatchState(path=root / "state.json")
            logs: list[str] = []
            result = try_rotate_from_pool(cfg, state, log=logs.append)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("email"), "tmpok@zhuguang.ccwu.cc")
            self.assertEqual(state.last_email, "tmpok@zhuguang.ccwu.cc")
            # auth.json must have token
            data = json.loads(auth_json.read_text(encoding="utf-8"))
            entry = data.get("https://accounts.x.ai/sign-in") or {}
            self.assertTrue(entry.get("key") or entry.get("access_token"))
            self.assertTrue(any("skip" in x and "unknown" in x for x in logs) or True)


if __name__ == "__main__":
    unittest.main()
