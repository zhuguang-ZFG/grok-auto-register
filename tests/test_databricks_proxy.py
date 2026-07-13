#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

import pytest

from databricks_pipeline import pool, schema
from databricks_pipeline.proxy_server import serve


@pytest.fixture()
def live_pool(tmp_path):
    auth = tmp_path / "a"
    dead = tmp_path / "d"
    auth.mkdir()
    dead.mkdir()
    cfg = {
        "auth_dir": str(auth),
        "dead_dir": str(dead),
        "proxy_port": 18320,
        "proxy_api_key": "sk-test",
        "probe_models": ["databricks-gpt-oss-120b"],
        "models_catalog_file": "databricks_pipeline/models_catalog.yaml",
        "probe_timeout_sec": 5,
        "_raw": {},
    }
    data = schema.new_credential(
        email="p@example.com",
        host="https://dbc.example.cloud.databricks.com",
        token="dapi" + "t" * 24,
        status="incomplete",
    )
    data["models"] = {"databricks-gpt-oss-120b": {"ok": True, "last_probe_at": "x"}}
    data["status"] = "live"
    pool.save_credential(data, cfg)
    return cfg


def test_proxy_models_and_chat(live_pool):
    cfg = live_pool
    httpd = serve(cfg, host="127.0.0.1")
    t = Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        conn = HTTPConnection("127.0.0.1", int(cfg["proxy_port"]), timeout=5)
        conn.request(
            "GET",
            "/v1/models",
            headers={"Authorization": "Bearer sk-test"},
        )
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        assert resp.status == 200
        assert body["object"] == "list"
        assert any(m["id"] for m in body["data"])

        fake = (
            200,
            {
                "id": "x",
                "choices": [
                    {"message": {"role": "assistant", "content": "pong"}, "index": 0}
                ],
            },
        )
        with patch("databricks_pipeline.proxy_server.forward_chat", return_value=fake):
            conn.request(
                "POST",
                "/v1/chat/completions",
                body=json.dumps(
                    {
                        "model": "databricks-gpt-oss-120b",
                        "messages": [{"role": "user", "content": "hi"}],
                    }
                ),
                headers={
                    "Authorization": "Bearer sk-test",
                    "Content-Type": "application/json",
                },
            )
            resp2 = conn.getresponse()
            body2 = json.loads(resp2.read().decode())
            assert resp2.status == 200
            assert body2["choices"][0]["message"]["content"] == "pong"
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
