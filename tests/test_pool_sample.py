import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pool_sample


class PoolSampleChatTests(unittest.TestCase):
    def test_chat_probe_quarantines_permission_denied(self):
        with tempfile.TemporaryDirectory() as td:
            auth_dir = Path(td)
            path = auth_dir / "xai-demo@example.com.json"
            path.write_text(
                json.dumps(
                    {
                        "email": "demo@example.com",
                        "access_token": "at",
                        "refresh_token": "rt",
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )
            cfg = {"cpa_auth_dir": str(auth_dir)}
            with mock.patch(
                "pool_sample._probe_chat_payload",
                return_value=("permission_denied", "403 denied"),
            ):
                result = pool_sample.sample_probe(cfg, sample_n=1, quarantine=True)
            self.assertEqual(result["live"], 0)
            self.assertEqual(result["dead"], 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["disabled"])
            self.assertEqual(data["quota_state"]["reason"], "permission-denied")

    def test_chat_probe_puts_exhausted_account_on_recoverable_hold(self):
        with tempfile.TemporaryDirectory() as td:
            auth_dir = Path(td)
            path = auth_dir / "xai-demo@example.com.json"
            path.write_text(
                json.dumps(
                    {
                        "email": "demo@example.com",
                        "access_token": "at",
                        "refresh_token": "rt",
                        "disabled": False,
                    }
                ),
                encoding="utf-8",
            )
            cfg = {"cpa_auth_dir": str(auth_dir)}
            with mock.patch(
                "pool_sample._probe_chat_payload",
                return_value=("quota_exhausted", "429 exhausted"),
            ):
                result = pool_sample.sample_probe(cfg, sample_n=1, quarantine=True)
            self.assertEqual(result["live"], 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["disabled"])
            self.assertGreater(data["quota_state"]["recover_after"], 0)


if __name__ == "__main__":
    unittest.main()
