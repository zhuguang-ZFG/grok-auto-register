"""Health probe result tracking and scoring engine for the Smart Router.

v1.1: per-upstream inflight limits, chat-TTFT probes, time-based circuit breaker
with half-open recovery (401 long cool / 429 exponential backoff).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# Circuit-breaker cooldowns (seconds).
COOLDOWN_HARD_SEC = 6 * 3600  # 401/403: long cool, do not thrash probes
COOLDOWN_SOFT_BASE_SEC = 30.0  # 429/5xx/timeout: starts at 30s
COOLDOWN_SOFT_MAX_SEC = 600.0  # cap soft cool at 10 min
FAIL_STREAK_OPEN = 3  # consecutive fails before opening the circuit

# Prefer openai-compat remotes over local CLIProxy when any remote looks live.
# Unprobed score is 0.01; a single ~3s success is ~0.33. Local free CPA is
# fallback-only so RR over dead/quota accounts does not drag p50.
REMOTE_PREFER_MIN_SCORE = 0.05


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
    # Concurrency cap. Local CLIProxy can take more; remote charity stations stay low.
    max_inflight: int = 3
    # Optional HTTP(S) proxy for this upstream (e.g. Clash http://127.0.0.1:7897).
    # Empty/None = direct. Local CLIProxy should stay direct.
    proxy_url: str | None = None


def is_local_fallback(upstream: Upstream) -> bool:
    """True for local CLIProxy catch-alls that should not beat healthy remotes."""
    name = (upstream.name or "").lower()
    return name == "local-cliproxy" or name.startswith("local-")


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
            # Circuit breaker
            "open_until": 0.0,  # epoch seconds; 0 = closed
            "cooldown_sec": 0.0,  # last applied cooldown length
            "half_open": False,  # one probe/request may try
            # Concurrency
            "inflight": 0,
        }

    def update(self, result: ProbeResult) -> None:
        """Incorporate a new probe/request result into the board."""
        if result.name not in self._upstreams:
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
        now = result.timestamp or time.time()

        if result.healthy:
            state["success_count"] += 1
            state["success_streak"] += 1
            state["fail_streak"] = 0
            state["ewma"].add(result.latency_ms)
            # Close the circuit on success.
            state["open_until"] = 0.0
            state["half_open"] = False
            state["cooldown_sec"] = 0.0
        else:
            state["fail_count"] += 1
            state["fail_streak"] += 1
            state["success_streak"] = 0
            self._open_circuit(state, result.status_code, now)

        state["last_status"] = result.status_code
        state["last_seen"] = now

    def _open_circuit(self, state: dict[str, Any], status_code: int, now: float) -> None:
        """Open or extend the circuit based on failure class.

        - 401 → hard cool for COOLDOWN_HARD_SEC (bad key / revoked).
        - 403 → soft cool (charity remotes flap 403 under load; not "dead").
        - 429/5xx/timeout → open after FAIL_STREAK_OPEN fails, then exponential
          backoff from COOLDOWN_SOFT_BASE_SEC up to COOLDOWN_SOFT_MAX_SEC.
        """
        if status_code == 401:
            cool = COOLDOWN_HARD_SEC
        elif status_code == 403:
            # Soft: community stations often 403 temporarily; 6h hard cool
            # permanently empties the router of every usable remote.
            prev = state.get("cooldown_sec") or 0.0
            cool = min(max(prev * 2.0, COOLDOWN_SOFT_BASE_SEC), COOLDOWN_SOFT_MAX_SEC)
        elif status_code == 429 or state["fail_streak"] > FAIL_STREAK_OPEN:
            prev = state.get("cooldown_sec") or 0.0
            if prev > 0 and (state.get("open_until") or 0) > now - 1:
                # Already open / re-fail during cool → double.
                cool = min(max(prev * 2.0, COOLDOWN_SOFT_BASE_SEC), COOLDOWN_SOFT_MAX_SEC)
            else:
                cool = COOLDOWN_SOFT_BASE_SEC
            if status_code == 429:
                cool = max(cool, COOLDOWN_SOFT_BASE_SEC)
        else:
            # Soft fail but streak not high enough to open yet.
            return

        state["cooldown_sec"] = cool
        state["open_until"] = now + cool
        state["half_open"] = False

    def _circuit_allows(self, name: str, now: float | None = None) -> bool:
        """Return True if the upstream may receive traffic right now."""
        state = self._state.get(name)
        if state is None:
            return False
        now = now if now is not None else time.time()
        open_until = state.get("open_until") or 0.0
        if open_until <= now:
            # Cooldown expired → half-open (allow one try) or fully closed.
            if open_until > 0 and not state.get("half_open"):
                state["half_open"] = True
            return True
        # Still open: only allow if already marked half-open (shouldn't happen
        # while open_until > now). Deny.
        return False

    def try_acquire(self, name: str, now: float | None = None) -> bool:
        """Atomically check circuit + inflight cap and reserve one slot.

        Returns True if the caller may send a request to this upstream.
        Caller MUST call ``release(name)`` when the request finishes.
        *now* is injectable for tests.
        """
        state = self._state.get(name)
        upstream = self._upstreams.get(name)
        if state is None or upstream is None:
            return False
        if not self._circuit_allows(name, now):
            return False
        if state["inflight"] >= upstream.max_inflight:
            return False
        # Half-open: only one inflight allowed even if max_inflight is higher.
        if state.get("half_open") and state["inflight"] >= 1:
            return False
        state["inflight"] += 1
        return True

    def release(self, name: str) -> None:
        """Release one inflight slot."""
        state = self._state.get(name)
        if state is None:
            return
        state["inflight"] = max(0, state["inflight"] - 1)

    def should_probe(self, name: str, now: float | None = None) -> bool:
        """Whether the health loop should probe this upstream now.

        Skips fully-open circuits (saves bandwidth on 401 graveyards).
        Allows half-open single probe after cooldown expires.
        """
        state = self._state.get(name)
        if state is None:
            return False
        now = now if now is not None else time.time()
        open_until = state.get("open_until") or 0.0
        if open_until <= now:
            return True  # closed or ready for half-open
        return False  # still cooling; skip probe

    def score(self, name: str, now: float | None = None) -> float:
        """Return the current score for an upstream, or 0.0 if unavailable."""
        state = self._state.get(name)
        upstream = self._upstreams.get(name)
        if state is None or upstream is None:
            return 0.0

        now = now if now is not None else time.time()
        if not self._circuit_allows(name, now):
            return 0.0

        # Legacy fail_streak hard-zero while circuit not yet timed.
        if state["fail_streak"] > FAIL_STREAK_OPEN and (state.get("open_until") or 0) > now:
            return 0.0

        total = state["success_count"] + state["fail_count"]
        if total == 0:
            # Unprobed: small positive score so first traffic can flow (fallback path).
            return 0.01

        success_rate = state["success_count"] / total
        if success_rate <= 0:
            # Fail-only: keep a tiny score while the circuit is closed so a single
            # bad probe does not permanently exile the only healthy remote.
            return 0.001

        ewma_value = state["ewma"].value
        if ewma_value is None or ewma_value <= 0:
            ewma_value = 1.0

        base = (success_rate * 1000.0) / max(ewma_value, 1.0)
        # Penalize high inflight so load spreads.
        load_penalty = 1.0 + (state["inflight"] / max(upstream.max_inflight, 1))
        return base / load_penalty

    def best(
        self,
        pool: str,
        model_alias: str,
        *,
        acquire: bool = False,
        exclude: set[str] | None = None,
        allow_local: bool = False,
    ) -> Upstream | None:
        """Pick the highest-score upstream for *pool* whose aliases include *model_alias*.

        If *acquire* is True, also reserve an inflight slot (try_acquire).
        If *model_alias* is empty, the alias filter is skipped so callers such as the
        router's ``/v1/models`` proxy can still obtain a viable upstream.

        By default, when any remote scores >= REMOTE_PREFER_MIN_SCORE, local
        CLIProxy is excluded (free CPA RR is too slow to be a peer hop).
        Pass *allow_local=True* only when remotes are all down.
        """
        skipped = exclude or set()
        candidates: list[tuple[float, Upstream]] = []
        for name, upstream in self._upstreams.items():
            if upstream.pool != pool:
                continue
            if name in skipped:
                continue
            if model_alias and model_alias not in upstream.aliases and "*" not in upstream.aliases:
                continue
            s = self.score(name)
            if s <= 0:
                continue
            candidates.append((s, upstream))

        candidates.sort(key=lambda t: t[0], reverse=True)
        remotes = [(s, u) for s, u in candidates if not is_local_fallback(u)]
        if not allow_local and any(s >= REMOTE_PREFER_MIN_SCORE for s, _ in remotes):
            # Healthy remotes available: skip local CPA so dead free accounts
            # on CLIProxy do not win RR / session stickiness.
            candidates = remotes
        elif not allow_local and remotes:
            # Remotes registered but cold/unprobed: still prefer them over local.
            candidates = remotes
        for _score, upstream in candidates:
            if acquire:
                if self.try_acquire(upstream.name):
                    return upstream
                continue
            return upstream
        return None

    def upstreams(self) -> list[Upstream]:
        """Return a snapshot of registered upstream definitions."""
        return list(self._upstreams.values())

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable view of the current board state."""
        now = time.time()
        out: dict[str, Any] = {}
        for name, upstream in self._upstreams.items():
            state = self._state[name]
            open_until = state.get("open_until") or 0.0
            out[name] = {
                "pool": upstream.pool,
                "base_url": upstream.base_url,
                "aliases": upstream.aliases,
                "score": self.score(name, now),
                "latency_ms": state["ewma"].value,
                "success_count": state["success_count"],
                "fail_count": state["fail_count"],
                "success_streak": state["success_streak"],
                "fail_streak": state["fail_streak"],
                "last_status": state["last_status"],
                "last_seen": state["last_seen"],
                "inflight": state["inflight"],
                "max_inflight": upstream.max_inflight,
                "open_until": open_until,
                "cooldown_remaining_sec": max(0.0, open_until - now),
                "half_open": bool(state.get("half_open")),
            }
        return out


async def async_probe(
    upstream: Upstream,
    client: httpx.AsyncClient,
    *,
    proxy_client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    """Probe *upstream* with a tiny chat completion (TTFT), not just /models.

    ``/models`` 200 is insufficient — charity stations often list models while
    chat is 401/429.  A 1-token completion measures real first-byte latency.

    *proxy_client* is used when the upstream has a proxy_url so probe path
    matches request path (Clash egress for remotes, direct for local).
    """
    base = upstream.base_url.rstrip("/")
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if upstream.headers:
        headers.update(upstream.headers)
    headers["Authorization"] = f"Bearer {upstream.api_key}"

    model = upstream.probe_model or next(
        (a for a in upstream.aliases if a != "*"), "grok-4.5"
    )
    # Local catch-all upstream: use grok-4.5 for chat probe.
    if model == "*" or not model:
        model = "grok-4.5"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }

    http = proxy_client or client
    start = time.monotonic()
    try:
        response = await http.post(url, headers=headers, json=body, timeout=15.0)
        latency_ms = (time.monotonic() - start) * 1000.0
        healthy = 200 <= response.status_code < 300
        err = "" if healthy else (response.text or "")[:120]
        return ProbeResult(
            name=upstream.name,
            pool=upstream.pool,
            url=upstream.base_url,
            healthy=healthy,
            latency_ms=latency_ms,
            status_code=response.status_code,
            error=err,
            timestamp=time.time(),
        )
    except Exception as exc:  # pragma: no cover - network errors vary
        latency_ms = (time.monotonic() - start) * 1000.0
        return ProbeResult(
            name=upstream.name,
            pool=upstream.pool,
            url=upstream.base_url,
            healthy=False,
            latency_ms=latency_ms,
            status_code=0,
            error=str(exc),
            timestamp=time.time(),
        )
