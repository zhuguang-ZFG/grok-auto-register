import io
import unittest

from scripts.import_cpa_with_probe import admit_candidate, classify_chat_result, probe_chat


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
        )
        self.assertEqual(status, "permission_denied")
        self.assertIsNone(candidate)
        self.assertEqual(refreshed, [])


if __name__ == "__main__":
    unittest.main()
