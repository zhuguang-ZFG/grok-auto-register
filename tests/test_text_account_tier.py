#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for text account tier scoring against the REAL AccountService.

Run with the gateway venv (has sqlalchemy):

  D:/Users/grok-auto-register/chatgpt2api/.venv/Scripts/python.exe tests/test_text_account_tier.py

Falls back to a source-structure smoke check when imports fail (no deps).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "chatgpt2api" / "services" / "account_service.py"


def _load_cls():
    sys.path.insert(0, str(ROOT / "chatgpt2api"))
    from services.account_service import AccountService  # type: ignore

    return AccountService


def test_tier_prefers_plus_go_with_rt():
    try:
        cls = _load_cls()
    except ImportError:
        # system python lacks gateway deps (sqlalchemy) — use venv interpreter
        try:
            import pytest  # type: ignore

            pytest.skip("gateway deps missing; run with chatgpt2api/.venv python")
        except ImportError:
            test_structure_when_no_import()
            return
    assert cls._text_account_tier({"refresh_token": "rt", "type": "plus"}) == 0
    assert cls._text_account_tier({"refresh_token": "rt", "type": "go"}) == 0
    assert cls._text_account_tier({"refresh_token": "rt", "type": "team"}) == 0
    assert cls._text_account_tier({"refresh_token": "rt", "type": "free"}) == 1
    assert cls._text_account_tier({"refresh_token": "rt", "type": "k12"}) == 1
    assert cls._text_account_tier({"refresh_token": "", "type": "k12"}) == 3
    assert cls._text_account_tier({"refresh_token": "", "type": "team"}) == 2
    assert cls._text_account_tier({"refresh_token": "", "type": ""}) == 2


def test_structure_when_no_import():
    text = SRC.read_text(encoding="utf-8")
    assert "def _text_account_tier" in text
    assert "healthy_by_tier" in text
    assert "raw_plan" in text
    assert re.search(r"preferred_plans", text)


if __name__ == "__main__":
    try:
        test_tier_prefers_plus_go_with_rt()
        print("real-import tests ok")
    except ImportError as e:
        print(f"import skipped ({e}); structure smoke only")
        test_structure_when_no_import()
        print("structure smoke ok")
