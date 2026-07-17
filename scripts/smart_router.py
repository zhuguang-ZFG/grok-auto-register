"""Smart Router main service for grok-auto-register."""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# Allow `from scripts.router_health import ...` when the script is executed
# directly (`python scripts/smart_router.py`) instead of as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.router_health import ProbeResult, ScoreBoard, Upstream, async_probe

DEFAULT_CONFIG_DIR = "D:/cli-proxy-api"

# Hop-by-hop headers should not be blindly forwarded between client and upstream.
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _join_url(base_url: str, path: str, query: str = "") -> str:
    """Combine an OpenAI-compatible *base_url* with the incoming request path.

    Configs store ``base_url`` with a trailing ``/v1`` (e.g.
    ``https://api.example.com/v1``), while the router receives requests at
    ``/v1/...``.  This helper strips the duplicate ``/v1`` segment so the
    forwarded URL stays correct.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[len("/v1") :]
    url = base + path
    if query:
        url = f"{url}?{query}"
    return url


def _upstream_name(pool: str, channel_name: str, key_index: int, alias: str) -> str:
    return f"{pool}/{channel_name}/{key_index}/{alias}"


def _load_openai_compat(pool: str, channels: list[dict] | None) -> list[Upstream]:
    """Parse ``openai-compatibility`` channel entries into Upstream objects."""
    upstreams: list[Upstream] = []
    for channel in channels or []:
        if channel.get("disabled"):
            continue
        channel_headers = channel.get("headers") or {}
        base_url = channel.get("base-url", "")
        channel_name = channel.get("name", "unknown")
        for key_index, entry in enumerate(channel.get("api-key-entries", [])):
            api_key = entry.get("api-key", "")
            for model in channel.get("models", []):
                alias = model.get("alias", "")
                if alias.startswith("remote-"):
                    continue
                name = _upstream_name(pool, channel_name, key_index, alias)
                upstreams.append(
                    Upstream(
                        name=name,
                        pool=pool,
                        base_url=base_url,
                        api_key=api_key,
                        headers=dict(channel_headers),
                        probe_model=model.get("name", ""),
                        aliases=[alias],
                    )
                )
    return upstreams


def _load_claude(pool: str, entries: list[dict] | None) -> list[Upstream]:
    """Parse ``claude-api-key`` entries into Upstream objects."""
    upstreams: list[Upstream] = []
    for index, entry in enumerate(entries or []):
        base_url = entry.get("base-url", "")
        if base_url and not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        api_key = entry.get("api-key", "")
        for model in entry.get("models", []):
            alias = model.get("alias", "")
            host = (
                base_url.replace("https://", "")
                .replace("http://", "")
                .replace("/", "_")
            )
            name = f"{pool}/{host}/{index}/{alias}"
            upstreams.append(
                Upstream(
                    name=name,
                    pool=pool,
                    base_url=base_url,
                    api_key=api_key,
                    headers=None,
                    probe_model=model.get("name", ""),
                    aliases=[alias],
                )
            )
    return upstreams




def _load_local_cliproxy(pool: str, data: dict) -> Upstream | None:
    """Create an upstream pointing at the local CLIProxy instance for *pool*.

    CLIProxy is the owner of the local credential pool (cpa_auths, k12,
    claude-api-key entries, etc.).  The Smart Router probes it like any other
    upstream and fails over to remote channels when it is slow or errors.
    """
    port = data.get("port")
    api_keys = data.get("api-keys") or []
    if not port or not api_keys:
        return None
    return Upstream(
        name=f"{pool}/local-cliproxy/0/*",
        pool=pool,
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key=str(api_keys[0]),
        headers=None,
        probe_model="",
        aliases=["*"],
    )


def load_pools(config_dir: Path | str) -> dict[str, dict]:
    """Parse ``D:/cli-proxy-api/config*.yaml`` into pool definitions.

    Returns ``{pool_name: {"port": int, "upstreams": [Upstream, ...]}}``.
    Task 2 only routes the Grok pool, so only ``config.yaml`` is loaded.
    Loading Codex/Claude/GLM configs would create upstreams that we neither
    probe nor proxy yet, wasting memory and bandwidth.
    """
    config_dir = Path(config_dir)
    pools: dict[str, dict] = {}
    path = config_dir / "config.yaml"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        port = data.get("port", 0)
        upstreams: list[Upstream] = []
        local = _load_local_cliproxy("grok", data)
        if local is not None:
            upstreams.append(local)
        if "openai-compatibility" in data:
            upstreams.extend(_load_openai_compat("grok", data["openai-compatibility"]))
        pools["grok"] = {"port": port, "upstreams": upstreams}
    return pools


class SmartRouter:
    """Transparent async proxy with health-aware upstream selection."""

    def __init__(self, config_dir: str = DEFAULT_CONFIG_DIR) -> None:
        self.config_dir = config_dir
        self.pools: dict[str, dict] = {}
        self.board = ScoreBoard()
        self.client: httpx.AsyncClient | None = None
        self._stop_event = asyncio.Event()

    async def setup(self) -> None:
        """Load pool configs and build the ScoreBoard."""
        self.pools = load_pools(self.config_dir)
        upstreams: list[Upstream] = []
        for pool in self.pools.values():
            upstreams.extend(pool.get("upstreams", []))
        self.board = ScoreBoard(upstreams)
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )

    async def close(self) -> None:
        """Stop the health loop and close the HTTP client."""
        self._stop_event.set()
        if self.client is not None:
            await self.client.aclose()

    async def health_loop(self, interval: float = 30.0) -> None:
        """Probe active upstreams every *interval* seconds.

        Task 2 only routes the Grok pool, so we restrict probing to Grok
        upstreams to avoid wasting bandwidth on Codex/Claude/GLM remotes.
        """
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        while not self._stop_event.is_set():
            try:
                await self._probe_all()
            except Exception:
                # Swallow probe-loop errors so a single bad upstream does not
                # crash the router.
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _probe_all(self) -> None:
        if self.client is None:
            return
        # Only probe Grok pool in this first pass.  Codex/Claude/GLM are still
        # served directly by CLIProxy on their public ports.
        grok = self.pools.get("grok", {})
        tasks = [
            self._probe_one(upstream)
            for upstream in grok.get("upstreams", [])
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_one(self, upstream: Upstream) -> None:
        if self.client is None:
            return
        result = await async_probe(upstream, self.client)
        self.board.update(result)

    def _extract_model_alias(self, request: Request, body: bytes) -> str:
        """Read the ``model`` field from a JSON request body if present."""
        if request.method not in ("POST", "PUT", "PATCH"):
            return ""
        try:
            data = json.loads(body)
        except Exception:
            return ""
        alias = data.get("model", "")
        return alias if isinstance(alias, str) else ""

    def _mark_failed(self, upstream: Upstream, status_code: int, error: str = "") -> None:
        """Record a request failure against an upstream in the ScoreBoard."""
        self.board.update(
            ProbeResult(
                name=upstream.name,
                pool=upstream.pool,
                url=upstream.base_url,
                healthy=False,
                latency_ms=0.0,
                status_code=status_code,
                error=error,
                timestamp=time.time(),
            )
        )

    def _fallback_upstream(
        self, pool: str, model_alias: str, tried: set[str]
    ) -> Upstream | None:
        """Return any matching upstream that has not been tried yet.

        This ensures the router can serve traffic before the first probe round
        completes, and gives exhausted scored-upstreams another chance.
        """
        for upstream in self.board.upstreams():
            if upstream.pool != pool:
                continue
            if model_alias and model_alias not in upstream.aliases:
                continue
            if upstream.name in tried:
                continue
            return upstream
        return None

    def _upstream_headers(
        self, request: Request, upstream: Upstream
    ) -> dict[str, str]:
        """Build headers for forwarding *request* to *upstream*.

        Channel-level headers from the config are applied first so the
        Bearer token derived from ``api-key-entries`` always wins.
        """
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() in ("host", "authorization"):
                continue
            headers[key] = value
        if upstream.headers:
            for key, value in upstream.headers.items():
                headers[key] = value
        # Remove any leftover Authorization variants so the upstream key is the
        # only one sent.
        for key in list(headers.keys()):
            if key.lower() == "authorization":
                del headers[key]
        headers["Authorization"] = f"Bearer {upstream.api_key}"
        return headers

    async def _build_response(
        self, response: httpx.Response, stream_ctx: Any | None = None
    ) -> Response:
        """Convert an httpx response into a Starlette response.

        For SSE responses the underlying *stream_ctx* is kept open and
        closed by the streaming iterator, yielding true pass-through.
        For all other responses the body is buffered and the context is
        closed before returning.
        """
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        # Remove content-encoding / content-length when we decode the body
        # ourselves; otherwise clients try to decompress plain bytes.
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type and stream_ctx is not None:
            async def body_iterator() -> AsyncGenerator[bytes, None]:
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                finally:
                    with contextlib.suppress(Exception):
                        await stream_ctx.__aexit__(None, None, None)

            return StreamingResponse(
                body_iterator(),
                status_code=response.status_code,
                headers=headers,
            )

        await response.aread()
        content = response.content
        if stream_ctx is not None:
            await stream_ctx.__aexit__(None, None, None)
        return Response(
            content=content,
            status_code=response.status_code,
            headers=headers,
        )

    async def proxy(self, request: Request, pool: str) -> Response:
        """Proxy *request* to the best upstream in *pool* with failover."""
        body = await request.body()
        model_alias = self._extract_model_alias(request, body)
        tried: set[str] = set()
        last_response: httpx.Response | None = None
        last_error: Exception | None = None

        # 1 initial attempt + up to 3 retries = max 4 upstream attempts.
        for _ in range(4):
            upstream = self.board.best(pool, model_alias)
            if upstream is None or upstream.name in tried:
                upstream = self._fallback_upstream(pool, model_alias, tried)
            if upstream is None:
                break
            tried.add(upstream.name)

            url = _join_url(
                upstream.base_url, request.url.path, request.url.query
            )
            headers = self._upstream_headers(request, upstream)
            stream_ctx = self.client.stream(
                request.method,
                url,
                headers=headers,
                content=body,
                timeout=30.0,
            )

            try:
                response = await stream_ctx.__aenter__()
            except httpx.TimeoutException as exc:
                last_error = exc
                self._mark_failed(upstream, 0, "timeout")
                continue
            except httpx.NetworkError as exc:
                last_error = exc
                self._mark_failed(upstream, 0, str(exc))
                continue
            except Exception as exc:  # pragma: no cover - defensive
                last_error = exc
                self._mark_failed(upstream, 0, str(exc))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_response = response
                last_error = None
                self._mark_failed(upstream, response.status_code)
                # Drain and close the response so we can retry or return it.
                await response.aread()
                await stream_ctx.__aexit__(None, None, None)
                continue

            if response.status_code >= 400:
                # 401/403/404 are hard-disable territory handled by
                # disable_bad_upstreams.py; return them directly without retry.
                return await self._build_response(response, stream_ctx)

            return await self._build_response(response, stream_ctx)

        if last_response is not None:
            return await self._build_response(last_response, None)
        if last_error is not None:
            return JSONResponse({"error": str(last_error)}, status_code=502)
        return JSONResponse({"error": "no upstream available"}, status_code=502)

    def snapshot(self) -> dict[str, Any]:
        """Return the current ScoreBoard snapshot."""
        return self.board.snapshot()


async def _proxy_handler(request: Request) -> Response:
    # Task 2 only exposes the Grok pool; additional pools are loaded but not
    # routed until the multi-port phase.
    router: SmartRouter = request.app.state.router
    return await router.proxy(request, "grok")


async def _status_handler(request: Request) -> JSONResponse:
    router: SmartRouter = request.app.state.router
    return JSONResponse(router.snapshot())


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette):
    router: SmartRouter = app.state.router
    await router.setup()
    health_task = asyncio.create_task(router.health_loop())
    yield
    router._stop_event.set()
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    await router.close()


def build_app(
    pools: dict | None = None,
    config_dir: str = DEFAULT_CONFIG_DIR,
    enable_lifespan: bool = False,
) -> Starlette:
    """Build a Starlette app wired to a ``SmartRouter``.

    *pools* is intended for tests.  When ``None``, upstreams are loaded from
    ``config_dir``.  *enable_lifespan* starts the background health probe loop
    and is used by the production entry point.
    """
    router = SmartRouter(config_dir=config_dir)
    if pools is not None:
        router.pools = pools
        router.board = ScoreBoard(
            [u for p in pools.values() for u in p.get("upstreams", [])]
        )
    else:
        router.pools = load_pools(config_dir)
        router.board = ScoreBoard(
            [u for p in router.pools.values() for u in p.get("upstreams", [])]
        )
    router.client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )

    kwargs: dict[str, Any] = {
        "routes": [
            Route(
                "/v1/{path:path}",
                _proxy_handler,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            ),
            Route("/router/status", _status_handler, methods=["GET"]),
        ],
    }
    if enable_lifespan:
        kwargs["lifespan"] = _lifespan
    app = Starlette(**kwargs)
    app.state.router = router
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(enable_lifespan=True), host="127.0.0.1", port=8317)
