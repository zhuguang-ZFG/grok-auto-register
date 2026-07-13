#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dahl_pipeline import quota


def test_day_cap(tmp_path: Path):
    p = tmp_path / "remint_state.json"
    assert quota.can_remint(5, path=p)
    for i in range(5):
        n = quota.record_remint(f"r{i}", path=p)
        assert n == i + 1
    assert quota.get_daily_count(path=p) == 5
    assert not quota.can_remint(5, path=p)
    snap = quota.status_snapshot(5, path=p)
    assert snap["remint_remaining_today"] == 0


def test_quota_error_heuristic():
    assert quota.is_quota_error(402, "")
    assert quota.is_quota_error(429, "slow down")
    assert quota.is_quota_error(403, "insufficient quota remaining")
    assert not quota.is_quota_error(200, "ok")
    assert not quota.is_quota_error(500, "internal")


def test_new_day_resets(tmp_path: Path):
    p = tmp_path / "st.json"
    # write yesterday-like state
    p.write_text(
        '{"day":"20000101","count":5,"events":[]}',
        encoding="utf-8",
    )
    assert quota.get_daily_count(path=p) == 0
    assert quota.can_remint(5, path=p)
