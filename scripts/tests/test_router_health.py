"""Tests for the Smart Router health probe and scoring engine."""
from __future__ import annotations

import pytest

from scripts.router_health import EwmaLatency, ProbeResult, ScoreBoard, Upstream


def test_ewma_prefers_recent_values() -> None:
    e = EwmaLatency(alpha=0.5)
    e.add(100.0)
    e.add(200.0)
    assert e.value == 150.0


def test_ewma_default_alpha() -> None:
    e = EwmaLatency()
    assert e.alpha == 0.3
    assert e.value is None


def test_ewma_multiple_updates() -> None:
    e = EwmaLatency(alpha=0.3)
    for value in (100.0, 100.0, 100.0):
        e.add(value)
    assert e.value == pytest.approx(100.0)


def test_scoreboard_picks_best_upstream() -> None:
    board = ScoreBoard(
        [
            Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"]),
            Upstream("b", "grok", "http://y/v1", "key-b", aliases=["grok-4.5"]),
        ]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", 1.0))
    board.update(ProbeResult("b", "grok", "http://y/v1", True, 200, 200, "", 1.0))
    best = board.best("grok", "grok-4.5")
    assert best is not None
    assert best.name == "a"


def test_scoreboard_alias_filter() -> None:
    board = ScoreBoard(
        [
            Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"]),
            Upstream("b", "grok", "http://y/v1", "key-b", aliases=["grok-4.5-mini"]),
        ]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", 1.0))
    board.update(ProbeResult("b", "grok", "http://y/v1", True, 10, 200, "", 1.0))
    best = board.best("grok", "grok-4.5-mini")
    assert best is not None
    assert best.name == "b"


def test_scoreboard_pool_filter() -> None:
    board = ScoreBoard(
        [
            Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"]),
            Upstream("b", "codex", "http://y/v1", "key-b", aliases=["gpt-4o"]),
        ]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", 1.0))
    board.update(ProbeResult("b", "codex", "http://y/v1", True, 10, 200, "", 1.0))
    best = board.best("codex", "gpt-4o")
    assert best is not None
    assert best.name == "b"


def test_scoreboard_fail_streak_cools_down() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", 1.0))
    assert board.score("a") > 0

    for _ in range(4):
        board.update(ProbeResult("a", "grok", "http://x/v1", False, 0, 503, "", 1.0))

    assert board.score("a") == 0.0
    assert board.best("grok", "grok-4.5") is None


def test_scoreboard_no_available_upstream_returns_none() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    assert board.best("grok", "grok-4.5") is None
    assert board.best("grok", "unknown-model") is None


def test_scoreboard_snapshot() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", 1.0))
    snap = board.snapshot()
    assert "a" in snap
    assert snap["a"]["pool"] == "grok"
    assert snap["a"]["score"] > 0
    assert snap["a"]["latency_ms"] == 50.0
    assert snap["a"]["success_streak"] == 1
