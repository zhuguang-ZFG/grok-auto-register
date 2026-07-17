"""Unit tests for probe_import_batch classify / select / apply_action."""
from __future__ import annotations

import json
import time
from pathlib import Path

import scripts.probe_import_batch as pib


def test_classify_matrix() -> None:
    assert pib.classify(200, "") == "ok"
    assert pib.classify(401, "unauthorized") == "unauthorized"
    assert pib.classify(403, "permission-denied") == "permission_denied"
    assert pib.classify(403, "other") == "forbidden"
    assert pib.classify(429, "free-usage-exhausted") == "quota"
    assert pib.classify(0, "timeout") == "network"
    assert pib.classify(500, "boom") == "http_500"


def test_select_files_enabled_only(tmp_path: Path, monkeypatch) -> None:
    auth = tmp_path / "cpa_auths"
    auth.mkdir()
    good = auth / "xai-good@x.com.json"
    bad = auth / "xai-bad@x.com.json"
    dis = auth / "xai-dis@x.com.json"
    good.write_text(json.dumps({"access_token": "at1", "disabled": False}), encoding="utf-8")
    bad.write_text(json.dumps({"email": "x"}), encoding="utf-8")  # no AT
    dis.write_text(
        json.dumps({"access_token": "at2", "disabled": True}), encoding="utf-8"
    )
    monkeypatch.setattr(pib, "AUTH_DIR", auth)
    files = pib.select_files(
        source=None, hours=24, include_disabled=False, enabled_only=True
    )
    assert [f.name for f in files] == ["xai-good@x.com.json"]


def test_apply_action_network_skips(tmp_path: Path, monkeypatch) -> None:
    auth = tmp_path / "cpa_auths"
    auth.mkdir()
    f = auth / "xai-a@x.com.json"
    f.write_text(
        json.dumps({"access_token": "at", "email": "a@x.com", "disabled": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pib, "AUTH_DIR", auth)
    assert pib.apply_action(f, "network") == "skip"
    assert pib.apply_action(f, "ok") == "none"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data.get("disabled") is not True


def test_apply_action_unauthorized_soft(tmp_path: Path, monkeypatch) -> None:
    auth = tmp_path / "cpa_auths"
    auth.mkdir()
    f = auth / "xai-b@x.com.json"
    f.write_text(
        json.dumps({"access_token": "at", "email": "b@x.com", "disabled": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pib, "AUTH_DIR", auth)

    def fake_soft(path: Path, reason: str, *, hours: float = 24.0) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["disabled"] = True
        data["quota_state"] = {
            "reason": "probe_or_refresh_fail",
            "detail": reason,
            "recover_after": time.time() + hours * 3600,
        }
        path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(pib, "soft_disable", fake_soft)
    assert pib.apply_action(f, "unauthorized") == "soft_unauthorized"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["disabled"] is True
    assert "recover_after" in data["quota_state"]
    # Must not invent RT refresh or dead-dir moves
    assert "refresh_token" not in data or data.get("refresh_token") is None
