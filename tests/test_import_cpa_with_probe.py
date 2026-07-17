import io
import unittest

from scripts.import_cpa_with_probe import admit_candidate, classify_chat_result, probe_chat, load_candidates, normalize


class ChatAdmissionClassificationTests(unittest.TestCase):
    def test_200_is_chat_ready(self):
        self.assertEqual(classify_chat_result(200, '{"choices":[]}'), "chat_ok")

    def test_permission_denied_is_terminal(self):
        body = '{"code":"permission-denied","error":"Access to the chat endpoint is denied"}'
        self.assertEqual(classify_chat_result(403, body), "permission_denied")

    def test_free_usage_exhausted_is_recoverable_hold(self):
        body = '{"code":"subscription:free-usage-exhausted"}'
        self.assertEqual(classify_chat_result(429, body), "quota_exhausted")

    def test_invalid_access_token_can_be_refreshed(self):
        self.assertEqual(classify_chat_result(401, '{"error":"invalid token"}'), "unauthorized")

    def test_unknown_http_error_stays_out_of_live_pool(self):
        self.assertEqual(classify_chat_result(500, '{"error":"upstream"}'), "http_error")


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"OK"}}]}'


class _Opener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return _Response()


class ChatProbeTests(unittest.TestCase):
    def test_probe_uses_chat_endpoint_and_cpa_headers(self):
        opener = _Opener()
        status, _body = probe_chat(
            {
                "access_token": "at-test",
                "base_url": "https://cli-chat-proxy.grok.com/v1",
                "headers": {"x-grok-client-version": "0.2.93"},
            },
            opener,
        )
        self.assertEqual(status, "chat_ok")
        self.assertEqual(opener.request.full_url, "https://cli-chat-proxy.grok.com/v1/chat/completions")
        self.assertEqual(opener.request.headers["Authorization"], "Bearer at-test")
        self.assertEqual(opener.request.headers["X-grok-client-version"], "0.2.93")
        self.assertEqual(opener.timeout, 30)


class CandidateAdmissionTests(unittest.TestCase):
    def test_live_at_does_not_consume_refresh_token(self):
        calls = []

        def chat(_d, _opener):
            calls.append("chat")
            return "chat_ok", "OK"

        def refresh(_d, _opener):
            calls.append("refresh")
            return "ok", {"access_token": "new"}

        status, candidate = admit_candidate(
            {"access_token": "old", "refresh_token": "rt"},
            object(),
            chat_probe=chat,
            refresher=refresh,
            warmup_sec=0,
        )
        self.assertEqual(status, "chat_ok")
        self.assertEqual(candidate["access_token"], "old")
        self.assertEqual(calls, ["chat"])

    def test_401_refreshes_once_then_rechecks_chat(self):
        calls = []

        def chat(d, _opener):
            calls.append(("chat", d["access_token"]))
            if d["access_token"] == "old":
                return "unauthorized", "401"
            return "chat_ok", "OK"

        def refresh(d, _opener):
            calls.append(("refresh", d["refresh_token"]))
            return "ok", {**d, "access_token": "new", "refresh_token": "new-rt"}

        status, candidate = admit_candidate(
            {"access_token": "old", "refresh_token": "rt"},
            object(),
            chat_probe=chat,
            refresher=refresh,
            warmup_sec=0,
        )
        self.assertEqual(status, "chat_ok")
        self.assertEqual(candidate["access_token"], "new")
        self.assertEqual(calls, [("chat", "old"), ("refresh", "rt"), ("chat", "new")])

    def test_permission_denied_never_refreshes(self):
        refreshed = []

        def refresh(_d, _opener):
            refreshed.append(True)
            return "ok", None

        status, candidate = admit_candidate(
            {"access_token": "old", "refresh_token": "rt"},
            object(),
            chat_probe=lambda _d, _o: ("permission_denied", "403"),
            refresher=refresh,
            warmup_sec=0,
            sleep_fn=lambda _s: None,
        )
        self.assertEqual(status, "permission_denied_quarantine")
        self.assertIsNotNone(candidate)
        self.assertEqual(refreshed, [])


if __name__ == "__main__":
    unittest.main()


from scripts.import_cpa_with_probe import load_candidates, normalize, email_from_tokens
import base64
import json
import os
import tarfile
import tempfile
from pathlib import Path


def _fake_jwt(payload: dict) -> str:
    head = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{head}.{body}.sig"


class NormalizeWashTests(unittest.TestCase):
    def test_force_cli_chat_proxy_and_headers(self):
        out = normalize(
            {
                "access_token": "a",
                "refresh_token": "r",
                "base_url": "https://api.x.ai/v1",
                "headers": None,
            },
            {},
        )
        self.assertEqual(out["base_url"], "https://cli-chat-proxy.grok.com/v1")
        self.assertEqual(out["headers"]["x-xai-token-auth"], "xai-grok-cli")
        self.assertIn("User-Agent", out["headers"])

    def test_email_from_jwt(self):
        at = _fake_jwt({"email": "user@example.com"})
        out = normalize(
            {
                "access_token": at,
                "refresh_token": "r",
                "base_url": "https://api.x.ai/v1",
            },
            {},
        )
        self.assertEqual(out.get("email"), "user@example.com")


class TarLoadTests(unittest.TestCase):
    def test_load_tar_gz_json(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            payload = {
                "access_token": "at",
                "refresh_token": "rt",
                "base_url": "https://api.x.ai/v1",
            }
            j = td_path / "xai-a.json"
            j.write_text(json.dumps(payload), encoding="utf-8")
            tar_path = td_path / "pack.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(j, arcname="authenticated/xai-a.json")
            items = load_candidates([tar_path])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["refresh_token"], "rt")


class WarmupAnd403RetryTests(unittest.TestCase):
    def test_warmup_called(self):
        slept = []

        def chat(_d, _o):
            return "chat_ok", "OK"

        status, cand = admit_candidate(
            {"access_token": "a", "refresh_token": "r"},
            object(),
            chat_probe=chat,
            warmup_sec=1.5,
            sleep_fn=lambda s: slept.append(s),
        )
        self.assertEqual(status, "chat_ok")
        self.assertEqual(slept, [1.5])

    def test_permission_denied_retries_then_out(self):
        calls = []
        slept = []

        def chat(_d, _o):
            calls.append("chat")
            return "permission_denied", "403"

        old_r = os.environ.get("ACPA_PERM_DENIED_RETRIES")
        old_s = os.environ.get("ACPA_PERM_DENIED_SLEEP_SEC")
        os.environ["ACPA_PERM_DENIED_RETRIES"] = "2"
        os.environ["ACPA_PERM_DENIED_SLEEP_SEC"] = "4"
        try:
            status, cand = admit_candidate(
                {"access_token": "a", "refresh_token": "r"},
                object(),
                chat_probe=chat,
                refresher=lambda *_: (_ for _ in ()).throw(AssertionError("no refresh")),
                warmup_sec=0,
                sleep_fn=lambda s: slept.append(s),
            )
        finally:
            if old_r is None:
                os.environ.pop("ACPA_PERM_DENIED_RETRIES", None)
            else:
                os.environ["ACPA_PERM_DENIED_RETRIES"] = old_r
            if old_s is None:
                os.environ.pop("ACPA_PERM_DENIED_SLEEP_SEC", None)
            else:
                os.environ["ACPA_PERM_DENIED_SLEEP_SEC"] = old_s
        self.assertEqual(status, "permission_denied_quarantine")
        self.assertIsNotNone(cand)
        self.assertEqual(len(calls), 3)  # initial + 2 retries
        self.assertEqual(slept, [4, 4])

