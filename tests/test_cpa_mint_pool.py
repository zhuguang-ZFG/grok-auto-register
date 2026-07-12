#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import unittest

from cpa_mint_pool import MintJob, MintPool, resolve_queue_max, resolve_worker_count


class MintPoolTests(unittest.TestCase):
    def test_resolve_workers(self):
        self.assertEqual(resolve_worker_count({"cpa_mint_workers": 3}), 3)
        self.assertEqual(resolve_worker_count({"concurrent_count": 2}), 2)
        self.assertEqual(resolve_worker_count({"concurrent_count": 8}), 4)
        self.assertEqual(resolve_worker_count({"cpa_mint_workers": 0}), 0)

    def test_queue_and_complete(self):
        pool = MintPool()
        seen = []

        def export_fn(email, password, sso=None, log_callback=None, page=None):
            seen.append(email)
            time.sleep(0.05)
            return {"ok": True, "path": f"{email}.json"}

        pool.ensure_started(
            workers=2, queue_max=4, export_fn=export_fn, write_local_fn=None
        )
        for i in range(3):
            ok = pool.submit(
                MintJob(email=f"u{i}@t.com", password="x", sso="s", delay_sec=0),
                block_sec=2,
            )
            self.assertTrue(ok)
        pool.wait_done(timeout=5)
        st = pool.stats()
        self.assertEqual(st["completed_ok"], 3)
        self.assertEqual(len(seen), 3)

    def test_backpressure_drop(self):
        pool = MintPool()
        gate = {"block": True}

        def export_fn(email, password, sso=None, log_callback=None, page=None):
            while gate["block"]:
                time.sleep(0.02)
            return {"ok": True, "path": "x"}

        pool.ensure_started(
            workers=1, queue_max=1, export_fn=export_fn, write_local_fn=None
        )
        self.assertTrue(
            pool.submit(
                MintJob(email="a@t.com", password="x", sso="s", delay_sec=0),
                block_sec=0.2,
            )
        )
        # fill the single queue slot while worker holds first job
        time.sleep(0.05)
        self.assertTrue(
            pool.submit(
                MintJob(email="b@t.com", password="x", sso="s", delay_sec=0),
                block_sec=0.2,
            )
        )
        dropped = pool.submit(
            MintJob(email="c@t.com", password="x", sso="s", delay_sec=0),
            block_sec=0.05,
        )
        self.assertFalse(dropped)
        self.assertGreaterEqual(pool.stats()["dropped"], 1)
        gate["block"] = False
        pool.wait_done(timeout=5)

    def test_resolve_queue_max(self):
        self.assertEqual(resolve_queue_max({}, 2), 4)
        self.assertEqual(resolve_queue_max({"cpa_mint_queue_max": 10}, 2), 10)


if __name__ == "__main__":
    unittest.main()
