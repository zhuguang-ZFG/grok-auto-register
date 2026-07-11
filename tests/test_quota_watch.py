import unittest

from quota_watch import (
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_QUOTA_PATTERNS,
    compile_patterns,
    flatten_log_line,
    line_matches,
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


if __name__ == "__main__":
    unittest.main()
