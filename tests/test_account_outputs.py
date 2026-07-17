import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from account_outputs import (
    append_account_line,
    queue_unsaved_account,
    retry_pending_file,
)


class AccountOutputsTests(unittest.TestCase):
    def test_append_deduplicates_by_email_and_sso(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"

            self.assertTrue(append_account_line(output, "a@example.com", "first", "sso-1"))
            self.assertFalse(append_account_line(output, "a@example.com", "changed", "sso-1"))
            self.assertTrue(append_account_line(output, "a@example.com", "third", "sso-2"))

            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                [
                    "a@example.com----first----sso-1",
                    "a@example.com----third----sso-2",
                ],
            )

    def test_queue_writes_pending_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            payload = {"email": "a@example.com", "password": "secret", "sso": "sso-1"}

            pending = queue_unsaved_account(output, payload, OSError("disk full"))

            self.assertEqual(pending, f"{output}.pending.jsonl")
            records = [json.loads(line) for line in Path(pending).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["email"], payload["email"])
            self.assertEqual(records[0]["password"], payload["password"])
            self.assertEqual(records[0]["sso"], payload["sso"])
            self.assertEqual(records[0]["output_path"], str(output))
            self.assertEqual(records[0]["error"], "disk full")

    def test_retry_is_idempotent_when_target_already_contains_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            append_account_line(output, "a@example.com", "secret", "sso-1")
            pending = queue_unsaved_account(
                output,
                {"email": "a@example.com", "password": "secret", "sso": "sso-1"},
                "previous failure",
            )

            summary = retry_pending_file(pending)

            self.assertEqual(summary["restored"], 1)
            self.assertEqual(summary["remaining"], 0)
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["a@example.com----secret----sso-1"],
            )
            self.assertEqual(Path(pending).read_text(encoding="utf-8"), "")

    def test_retry_retains_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts.txt"
            pending = Path(tmp) / "pending.jsonl"
            valid = {
                "email": "a@example.com",
                "password": "secret",
                "sso": "sso-1",
                "output_path": str(output),
                "error": "previous failure",
            }
            pending.write_text("not-json\n" + json.dumps(valid) + "\n", encoding="utf-8")

            summary = retry_pending_file(pending)

            self.assertEqual(summary["restored"], 1)
            self.assertEqual(summary["remaining"], 1)
            self.assertEqual(pending.read_text(encoding="utf-8"), "not-json\n")
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                ["a@example.com----secret----sso-1"],
            )

    def test_retry_rejects_same_pending_and_target_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending.jsonl"
            pending.write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                retry_pending_file(pending, output_path=pending)

    def test_retry_acquires_pending_and_target_locks_in_sorted_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "z-pending.jsonl"
            output = Path(tmp) / "a-accounts.txt"
            pending.write_text(
                json.dumps(
                    {"email": "a@example.com", "password": "secret", "sso": "sso-1"}
                )
                + "\n",
                encoding="utf-8",
            )
            acquired = []

            class RecordingLock:
                def __init__(self, path, *args, **kwargs):
                    self.path = os.fspath(path)

                def __enter__(self):
                    acquired.append(self.path)
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            with mock.patch("account_outputs.FileLock", RecordingLock):
                retry_pending_file(pending, output_path=output)

            expected = sorted([f"{pending}.lock", f"{output}.lock"])
            self.assertEqual(acquired[:2], expected)


if __name__ == "__main__":
    unittest.main()
