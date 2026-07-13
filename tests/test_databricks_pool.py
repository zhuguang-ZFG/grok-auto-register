#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from databricks_pipeline import pool, schema
from databricks_pipeline.config import get_databricks_section


@pytest.fixture()
def tmp_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    auth = tmp_path / "databricks_auths"
    dead = tmp_path / "databricks_auths_dead"
    auth.mkdir()
    dead.mkdir()
    cfg = get_databricks_section()
    cfg = dict(cfg)
    cfg["auth_dir"] = str(auth)
    cfg["dead_dir"] = str(dead)
    cfg["max_per_day"] = 5
    # pool.resolve uses ROOT-relative; monkeypatch resolve via absolute paths already
    return cfg


def test_live_requires_probe(tmp_cfg):
    data = schema.new_credential(
        email="a@b.com",
        host="https://dbc.example.cloud.databricks.com",
        token="dapi" + "x" * 24,
        status="incomplete",
    )
    data["status"] = "live"
    with pytest.raises(ValueError):
        pool.save_credential(data, tmp_cfg)


def test_save_and_list(tmp_cfg):
    data = schema.new_credential(email="u@example.com", status="incomplete")
    path = pool.save_credential(data, tmp_cfg)
    assert path.is_file()
    rows = pool.list_credentials(tmp_cfg)
    assert len(rows) == 1
    assert rows[0]["email"] == "u@example.com"


def test_day_cap(tmp_cfg):
    assert pool.can_register_more(tmp_cfg, 1)
    for _ in range(5):
        pool.incr_daily_count(tmp_cfg)
    assert pool.get_daily_count(tmp_cfg) == 5
    assert not pool.can_register_more(tmp_cfg, 1)


def test_expired_not_selectable(tmp_cfg):
    data = schema.new_credential(
        email="old@example.com",
        host="https://dbc.example.cloud.databricks.com",
        token="dapi" + "y" * 24,
        status="incomplete",
    )
    data["models"] = {"databricks-gpt-oss-120b": {"ok": True}}
    data["status"] = "live"
    data["trial_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    pool.save_credential(data, tmp_cfg)
    assert schema.is_expired(data)
    assert not schema.selectable(data)
