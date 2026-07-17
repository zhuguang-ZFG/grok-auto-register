"""Tests for registration result reliability (persist-first + pending recovery)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import account_outputs
import grok_register_ttk as grt


class PersistRegisteredAccountTests(unittest.TestCase):
    def test_primary_append_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            result = grt._persist_registered_account(
                output, "a@example.com", "pw", "sso-1"
            )
            self.assertTrue(result)
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["a@example.com----pw----sso-1"],
            )

    def test_duplicate_primary_append_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            grt._persist_registered_account(output, "a@example.com", "pw", "sso-1")
            result = grt._persist_registered_account(
                output, "a@example.com", "different", "sso-1"
            )
            self.assertTrue(result)
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["a@example.com----pw----sso-1"],
            )

    def test_primary_failure_queues_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            output.mkdir()  # force append_account_line to fail
            result = grt._persist_registered_account(
                output, "a@example.com", "pw", "sso-1"
            )
            self.assertFalse(result)
            pending = Path(f"{output}.pending.jsonl")
            self.assertTrue(pending.exists())
            records = [
                json.loads(line)
                for line in pending.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["email"], "a@example.com")
            self.assertEqual(records[0]["password"], "pw")
            self.assertEqual(records[0]["sso"], "sso-1")
            self.assertTrue(
                "Is a directory" in records[0]["error"]
                or "Permission denied" in records[0]["error"]
            )

    def test_both_primary_and_pending_failure_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            output.mkdir()
            pending = Path(f"{output}.pending.jsonl")
            pending.mkdir()  # force pending write to fail too
            with self.assertRaises(RuntimeError):
                grt._persist_registered_account(
                    output, "a@example.com", "pw", "sso-1"
                )


class PostRegisterPipelineTests(unittest.TestCase):
    def test_returns_structured_results(self):
        with mock.patch(
            "grok_register_ttk.add_token_to_token_only_file", return_value=True
        ), mock.patch(
            "grok_register_ttk.add_token_to_grok2api_pools"
        ) as pools_mock, mock.patch(
            "grok_register_ttk.write_local_grok_from_cpa", return_value="auth.json"
        ):
            results = grt.run_post_register_pipeline(
                "sso-1", "a@example.com", cpa_result={"file": "x"}
            )
            self.assertTrue(results["token_file"])
            self.assertTrue(results["grok2api_pools"])
            self.assertEqual(results["local_grok"], "auth.json")
            self.assertEqual(results["warnings"], [])
            pools_mock.assert_called_once_with(
                "sso-1", email="a@example.com", log_callback=None
            )

    def test_isolates_step_failures_as_warnings(self):
        with mock.patch(
            "grok_register_ttk.add_token_to_token_only_file",
            side_effect=OSError("disk full"),
        ), mock.patch(
            "grok_register_ttk.add_token_to_grok2api_pools",
            side_effect=OSError("net down"),
        ), mock.patch(
            "grok_register_ttk.write_local_grok_from_cpa",
            side_effect=OSError("cpa fail"),
        ):
            results = grt.run_post_register_pipeline(
                "sso-1", "a@example.com", cpa_result={"file": "x"}
            )
            self.assertIsNone(results["token_file"])
            self.assertFalse(results["grok2api_pools"])
            self.assertIsNone(results["local_grok"])
            self.assertEqual(len(results["warnings"]), 3)
            self.assertTrue(all("disk full" in w or "net down" in w or "cpa fail" in w for w in results["warnings"]))

    def test_missing_cpa_result_skips_local_grok(self):
        with mock.patch(
            "grok_register_ttk.add_token_to_token_only_file", return_value=True
        ), mock.patch(
            "grok_register_ttk.add_token_to_grok2api_pools"
        ), mock.patch(
            "grok_register_ttk.write_local_grok_from_cpa"
        ) as local_mock:
            results = grt.run_post_register_pipeline("sso-1", "a@example.com")
            self.assertIsNone(results["local_grok"])
            local_mock.assert_not_called()


class _FakeApp:
    def __init__(self, output_file):
        self.accounts_output_file = output_file
        self.results = []
        self.success_count = 0

    def should_stop(self):
        return False


class GuiBodyPersistOrderTests(unittest.TestCase):
    def _register_body(self, app, cpa_side_effect=None, nsfw_side_effect=None):
        cpa_kwargs = {"side_effect": cpa_side_effect} if cpa_side_effect is not None else {"return_value": {"file": "x"}}
        nsfw_kwargs = {"side_effect": nsfw_side_effect} if nsfw_side_effect is not None else {"return_value": (True, "ok")}
        with mock.patch("grok_register_ttk.open_signup_page"), mock.patch(
            "grok_register_ttk.fill_email_and_submit",
            return_value=("a@example.com", "dev-token"),
        ), mock.patch(
            "grok_register_ttk.fill_code_and_submit", return_value="123456"
        ), mock.patch(
            "grok_register_ttk.fill_profile_and_submit",
            return_value={
                "password": "pw",
                "given_name": "A",
                "family_name": "B",
            },
        ), mock.patch(
            "grok_register_ttk.wait_for_sso_cookie", return_value="sso-1"
        ), mock.patch(
            "grok_register_ttk._get_page", return_value=mock.Mock()
        ), mock.patch(
            "grok_register_ttk._enqueue_cpa_mint", **cpa_kwargs
        ) as cpa_mock, mock.patch(
            "grok_register_ttk.enable_nsfw_for_token", **nsfw_kwargs
        ), mock.patch(
            "grok_register_ttk.run_post_register_pipeline"
        ), mock.patch(
            "grok_register_ttk.config", {"enable_nsfw": True}
        ):
            return grt.GrokRegisterGUI._register_one_account_body(
                app, print, worker_id=0
            ), cpa_mock

    def test_persists_immediately_after_sso(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            app = _FakeApp(str(output))
            with mock.patch(
                "grok_register_ttk._persist_registered_account"
            ) as persist_mock:
                self._register_body(app)
                persist_mock.assert_called_once_with(
                    str(output),
                    "a@example.com",
                    "pw",
                    "sso-1",
                    log_callback=print,
                )
                output.write_text(
                    "a@example.com----pw----sso-1\n", encoding="utf-8"
                )

    def test_cpa_exception_does_not_lose_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            app = _FakeApp(str(output))
            calls = []

            def fake_persist(path, email, password, sso, log_callback=None):
                calls.append(("persist", email, sso))
                return True

            def fake_cpa(*args, **kwargs):
                calls.append(("cpa",))
                raise RuntimeError("turnstile blocked")

            with mock.patch(
                "grok_register_ttk._persist_registered_account", side_effect=fake_persist
            ):
                with self.assertRaises(RuntimeError):
                    self._register_body(app, cpa_side_effect=fake_cpa)
            self.assertEqual(calls[0], ("persist", "a@example.com", "sso-1"))
            self.assertEqual(calls[1], ("cpa",))


class CliBodyPersistOrderTests(unittest.TestCase):
    def _register_body(self, output_file, cpa_side_effect=None, nsfw_side_effect=None):
        cpa_kwargs = {"side_effect": cpa_side_effect} if cpa_side_effect is not None else {"return_value": {"file": "x"}}
        nsfw_kwargs = {"side_effect": nsfw_side_effect} if nsfw_side_effect is not None else {"return_value": (True, "ok")}
        with mock.patch("grok_register_ttk.open_signup_page"), mock.patch(
            "grok_register_ttk.fill_email_and_submit",
            return_value=("a@example.com", "dev-token"),
        ), mock.patch(
            "grok_register_ttk.fill_code_and_submit", return_value="123456"
        ), mock.patch(
            "grok_register_ttk.fill_profile_and_submit",
            return_value={
                "password": "pw",
                "given_name": "A",
                "family_name": "B",
            },
        ), mock.patch(
            "grok_register_ttk.wait_for_sso_cookie", return_value="sso-1"
        ), mock.patch(
            "grok_register_ttk._get_page", return_value=mock.Mock()
        ), mock.patch(
            "grok_register_ttk._enqueue_cpa_mint", **cpa_kwargs
        ) as cpa_mock, mock.patch(
            "grok_register_ttk.enable_nsfw_for_token", **nsfw_kwargs
        ), mock.patch(
            "grok_register_ttk.run_post_register_pipeline"
        ), mock.patch(
            "grok_register_ttk.config", {"enable_nsfw": True}
        ):
            return grt._register_one_account_cli_body(
                print, lambda: False, str(output_file), clash_node=None
            ), cpa_mock

    def test_persists_immediately_after_sso(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            with mock.patch(
                "grok_register_ttk._persist_registered_account"
            ) as persist_mock:
                self._register_body(output)
                persist_mock.assert_called_once_with(
                    str(output),
                    "a@example.com",
                    "pw",
                    "sso-1",
                    log_callback=print,
                )

    def test_nsfw_failure_does_not_lose_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            calls = []

            def fake_persist(path, email, password, sso, log_callback=None):
                calls.append(("persist", email, sso))
                return True

            with mock.patch(
                "grok_register_ttk._persist_registered_account", side_effect=fake_persist
            ):
                self._register_body(output, nsfw_side_effect=lambda *a, **k: (False, "blocked"))
            self.assertEqual(calls[0], ("persist", "a@example.com", "sso-1"))


class RetryPendingCliTests(unittest.TestCase):
    def test_retry_pending_command(self):
        with mock.patch.object(
            account_outputs,
            "retry_pending_file",
            return_value={"restored": 2, "remaining": 1, "output": "out.txt"},
        ) as retry_mock, mock.patch.object(
            sys, "argv", ["grok_register_ttk.py", "retry-pending", "pending.jsonl", "out.txt"]
        ):
            grt.main()
            retry_mock.assert_called_once_with(
                "pending.jsonl", output_path="out.txt", log_callback=print
            )

    def test_retry_pending_without_path_exits(self):
        with mock.patch.object(
            sys, "argv", ["grok_register_ttk.py", "retry-pending"]
        ):
            with self.assertRaises(SystemExit):
                grt.main()


if __name__ == "__main__":
    unittest.main()
