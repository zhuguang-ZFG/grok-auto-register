"""Health probe result tracking and scoring engine for the Smart Router."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Single measurement from probing an upstream."""

    name: str
    pool: str
    url: str
    healthy: bool
    latency_ms: float
    status_code: int
    error: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class Upstream:
    """Static configuration for an upstream that Task 2 uses to forward requests."""

    name: str
    pool: str
    base_url: str
    api_key: str
    headers: dict[str, str] | None = None
    probe_model: str = ""
    aliases: list[str] = field(default_factory=list)


class EwmaLatency:
    """Exponentially weighted moving average for latency in milliseconds."""

    def __init__(self, alpha: float = 0.3) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be between 0 and 1")
        self.alpha = alpha
        self.value: float | None = None

    def add(self, latency_ms: float) -> None:
        """Update the EWMA with a new latency sample."""
        if self.value is None:
            self.value = latency_ms
        else:
            self.value = self.alpha * latency_ms + (1.0 - self.alpha) * self.value


class ScoreBoard:
    """Maintains per-upstream health state and selects the best upstream for a request."""

    def __init__(self, upstreams: list[Upstream] | None = None) -> None:
        self._upstreams: dict[str, Upstream] = {}
        self._state: dict[str, dict[str, Any]] = {}
        if upstreams:
            for upstream in upstreams:
                self.register(upstream)

    def register(self, upstream: Upstream) -> None:
        """Register a static upstream definition."""
        self._upstreams[upstream.name] = upstream
        if upstream.name not in self._state:
            self._state[upstream.name] = self._empty_state()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "ewma": EwmaLatency(),
            "success_count": 0,
            "fail_count": 0,
            "success_streak": 0,
            "fail_streak": 0,
            "last_status": None,
            "last_seen": None,
        }

    def update(self, result: ProbeResult) -> None:
        """Incorporate a new probe result into the board."""
        if result.name not in self._upstreams:
            # Unknown upstreams are auto-registered with a placeholder config so the
            # board can still reason about them. Task 2 will register real configs.
            self.register(
                Upstream(
                    name=result.name,
                    pool=result.pool,
                    base_url=result.url,
                    api_key="",
                    aliases=[],
                )
            )

        state = self._state[result.name]
        if result.healthy:
            state["success_count"] += 1
            state["success_streak"] += 1
            state["fail_streak"] = 0
            state["ewma"].add(result.latency_ms)
        else:
            state["fail_count"] += 1
            state["fail_streak"] += 1
            state["success_streak"] = 0
        state["last_status"] = result.status_code
        state["last_seen"] = result.timestamp

    def score(self, name: str) -> float:
        """Return the current score for an upstream, or 0.0 if unavailable."""
        state = self._state.get(name)
        if state is None:
            return 0.0

        if state["fail_streak"] > 3:
            return 0.0

        total = state["success_count"] + state["fail_count"]
        if total == 0:
            return 0.0

        success_rate = state["success_count"] / total
        ewma_value = state["ewma"].value
        if ewma_value is None or ewma_value <= 0:
            ewma_value = 1.0

        return (success_rate * 1000.0) / max(ewma_value, 1.0)

    def best(self, pool: str, model_alias: str) -> Upstream | None:
        """Pick the highest-score upstream for *pool* whose aliases include *model_alias*."""
        best_upstream: Upstream | None = None
        best_score = 0.0

        for name, upstream in self._upstreams.items():
            if upstream.pool != pool:
                continue
            if model_alias not in upstream.aliases:
                continue

            s = self.score(name)
            if s > best_score:
                best_score = s
                best_upstream = upstream

        return best_upstream

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable view of the current board state."""
        out: dict[str, Any] = {}
        for name, upstream in self._upstreams.items():
            state = self._state[name]
            out[name] = {
                "pool": upstream.pool,
                "base_url": upstream.base_url,
                "aliases": upstream.aliases,
                "score": self.score(name),
                "latency_ms": state["ewma"].value,
                "success_count": state["success_count"],
                "fail_count": state["fail_count"],
                "success_streak": state["success_streak"],
                "fail_streak": state["fail_streak"],
                "last_status": state["last_status"],
                "last_seen": state["last_seen"],
            }
        return out
