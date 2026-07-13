#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock, patch

from databricks_pipeline import probe, schema


def test_resolve_alias():
    # catalog file may map system.ai.*
    name = probe.resolve_model_name("databricks-gpt-oss-120b")
    assert name == "databricks-gpt-oss-120b"


def test_probe_success_marks_live():
    data = schema.new_credential(
        email="t@example.com",
        host="https://dbc.example.cloud.databricks.com",
        token="dapi" + "z" * 24,
        status="incomplete",
    )
    cfg = {
        "probe_models": ["databricks-gpt-oss-120b"],
        "probe_timeout_sec": 5,
        "models_catalog_file": "databricks_pipeline/models_catalog.yaml",
        "_raw": {},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "hi"}}]
    }
    mock_resp.text = "{}"

    with patch("databricks_pipeline.probe.requests.post", return_value=mock_resp):
        out = probe.probe_credential(data, cfg)

    assert out["status"] == "live"
    assert out["models"]["databricks-gpt-oss-120b"]["ok"] is True


def test_probe_all_fail_soft_disable():
    data = schema.new_credential(
        email="t2@example.com",
        host="https://dbc.example.cloud.databricks.com",
        token="dapi" + "z" * 24,
        status="incomplete",
    )
    cfg = {
        "probe_models": ["databricks-gpt-oss-120b"],
        "probe_timeout_sec": 5,
        "models_catalog_file": "databricks_pipeline/models_catalog.yaml",
        "_raw": {},
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "missing"
    mock_resp.json.return_value = {}

    with patch("databricks_pipeline.probe.requests.post", return_value=mock_resp):
        out = probe.probe_credential(data, cfg)

    assert out["status"] == "soft_disabled"
    assert out["disable_reason"] == "probe_all_failed"
