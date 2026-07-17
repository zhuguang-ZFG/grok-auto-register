"""Tests for the Smart Router health probe and scoring engine (v1.1)."""
from __future__ import annotations

import time

import pytest

from scripts.router_health import (
    COOLDOWN_HARD_SEC,
    COOLDOWN_SOFT_BASE_SEC,
    FAIL_STREAK_OPEN,
    EwmaLatency,
    ProbeResult,
    ScoreBoard,
    Upstream,
)


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
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", time.time()))
    assert board.score("a") > 0

    now = time.time()
    for _ in range(FAIL_STREAK_OPEN + 1):
        board.update(ProbeResult("a", "grok", "http://x/v1", False, 0, 503, "", now))

    assert board.score("a") == 0.0
    assert board.best("grok", "grok-4.5") is None
    # Soft circuit should be open.
    snap = board.snapshot()
    assert snap["a"]["cooldown_remaining_sec"] >= COOLDOWN_SOFT_BASE_SEC - 1


def test_scoreboard_no_available_upstream_returns_none() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    # Unprobed still has tiny score 0.01 so best returns it (cold-start path).
    best = board.best("grok", "grok-4.5")
    assert best is not None
    assert best.name == "a"
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
    assert "inflight" in snap["a"]
    assert "cooldown_remaining_sec" in snap["a"]


def test_hard_401_opens_long_circuit() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    now = time.time()
    board.update(ProbeResult("a", "grok", "http://x/v1", False, 0, 401, "bad key", now))
    assert board.score("a") == 0.0
    assert board.should_probe("a", now) is False
    snap = board.snapshot()
    assert snap["a"]["cooldown_remaining_sec"] > COOLDOWN_HARD_SEC - 5


def test_soft_circuit_half_open_after_cooldown() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"], max_inflight=2)]
    )
    now = time.time()
    for _ in range(FAIL_STREAK_OPEN + 1):
        board.update(ProbeResult("a", "grok", "http://x/v1", False, 0, 503, "", now))
    assert board.should_probe("a", now) is False

    # Jump past soft cooldown.
    future = now + COOLDOWN_SOFT_BASE_SEC + 1
    assert board.should_probe("a", future) is True
    # Half-open: one acquire allowed.
    assert board.try_acquire("a", now=future) is True
    # Second acquire while half-open blocked.
    assert board.try_acquire("a", now=future) is False
    board.release("a")


def test_inflight_cap_blocks_acquire() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"], max_inflight=2)]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", time.time()))
    assert board.try_acquire("a") is True
    assert board.try_acquire("a") is True
    assert board.try_acquire("a") is False  # cap 2
    board.release("a")
    assert board.try_acquire("a") is True
    board.release("a")
    board.release("a")


def test_best_acquire_skips_full_upstream() -> None:
    board = ScoreBoard(
        [
            Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"], max_inflight=1),
            Upstream("b", "grok", "http://y/v1", "key-b", aliases=["grok-4.5"], max_inflight=1),
        ]
    )
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 50, 200, "", time.time()))
    board.update(ProbeResult("b", "grok", "http://y/v1", True, 200, 200, "", time.time()))
    # a is faster → preferred, fill it.
    first = board.best("grok", "grok-4.5", acquire=True)
    assert first is not None and first.name == "a"
    # next request should get b because a is at cap.
    second = board.best("grok", "grok-4.5", acquire=True)
    assert second is not None and second.name == "b"
    board.release("a")
    board.release("b")


def test_success_closes_circuit() -> None:
    board = ScoreBoard(
        [Upstream("a", "grok", "http://x/v1", "key-a", aliases=["grok-4.5"])]
    )
    now = time.time()
    board.update(ProbeResult("a", "grok", "http://x/v1", False, 0, 401, "", now))
    assert board.score("a") == 0.0
    # Success after cool still closes immediately when we force a success update.
    board.update(ProbeResult("a", "grok", "http://x/v1", True, 40, 200, "", now + 1))
    assert board.score("a") > 0
    assert board.snapshot()["a"]["cooldown_remaining_sec"] == 0.0


def test_best_skips_local_when_remote_healthy() -> None:
    """Healthy remotes beat local-cliproxy even if local EWMA looks faster."""
    board = ScoreBoard(
        [
            Upstream(
                "local-cliproxy",
                "grok",
                "http://127.0.0.1:8318/v1",
                "local",
                aliases=["grok-4.5", "*"],
            ),
            Upstream("pub8317", "grok", "http://remote/v1", "k", aliases=["grok-4.5"]),
        ]
    )
    # Local "faster" on paper but still must lose when remote is live.
    board.update(
        ProbeResult("local-cliproxy", "grok", "http://127.0.0.1:8318/v1", True, 80, 200, "", 1.0)
    )
    board.update(
        ProbeResult("pub8317", "grok", "http://remote/v1", True, 3000, 200, "", 1.0)
    )
    best = board.best("grok", "grok-4.5")
    assert best is not None
    assert best.name == "pub8317"


def test_best_skips_local_when_remotes_cold() -> None:
    """Unprobed remotes still beat local (local is last-resort only)."""
    board = ScoreBoard(
        [
            Upstream(
                "local-cliproxy",
                "grok",
                "http://127.0.0.1:8318/v1",
                "local",
                aliases=["grok-4.5", "*"],
            ),
            Upstream("cold-remote", "grok", "http://remote/v1", "k", aliases=["grok-4.5"]),
        ]
    )
    board.update(
        ProbeResult("local-cliproxy", "grok", "http://127.0.0.1:8318/v1", True, 100, 200, "", 1.0)
    )
    best = board.best("grok", "grok-4.5")
    assert best is not None
    assert best.name == "cold-remote"
