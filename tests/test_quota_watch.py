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


if __name__ == "__main__":
    unittest.main()
