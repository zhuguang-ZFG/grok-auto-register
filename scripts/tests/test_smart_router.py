"""Tests for the Smart Router main service."""
from __future__ import annotations

import asyncio

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from scripts.router_health import Upstream
from scripts.smart_router import SmartRouter, build_app, load_pools


def _test_client(app: Starlette) -> httpx.AsyncClient:
    """Return an httpx client backed by an ASGI transport.

    httpx 0.28 removed the ``app=`` shortcut, so we use ``ASGITransport``
    explicitly.
    """
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_router_no_upstream_502() -> None:
    async def _run() -> None:
        app = build_app({"grok": {"port": 8318, "upstreams": []}})
        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})
        assert r.status_code == 502
        assert "no upstream" in r.text.lower()

    asyncio.run(_run())


def test_router_proxy_selects_best() -> None:
    async def _run() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json={"model": "grok-4.5", "choices": []})

        pools = {
            "grok": {
                "port": 8318,
                "upstreams": [
                    Upstream("ok", "grok", "http://mock/v1", "key", aliases=["grok-4.5"])
                ],
            }
        }
        app = build_app(pools)
        app.state.router.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})

        assert r.status_code == 200
        assert r.json() == {"model": "grok-4.5", "choices": []}
        assert calls == ["/v1/chat/completions"]

    asyncio.run(_run())


def test_router_failover_on_429() -> None:
    async def _run() -> None:
        calls: list[str] = []
        responses = [
            (429, {}, b'{"error": "rate limited"}'),
            (200, {}, b'{"ok": true}'),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            status, headers, body = responses[len(calls) - 1]
            return httpx.Response(status, headers=headers, content=body)

        pools = {
            "grok": {
                "port": 8318,
                "upstreams": [
                    Upstream("bad", "grok", "http://mock/v1", "key", aliases=["grok-4.5"]),
                    Upstream("good", "grok", "http://mock/v1", "key", aliases=["grok-4.5"]),
                ],
            }
        }
        app = build_app(pools)
        app.state.router.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})

        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert len(calls) == 2

    asyncio.run(_run())


def test_router_failover_on_timeout() -> None:
    async def _run() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if len(calls) == 1:
                raise httpx.TimeoutException("timed out")
            return httpx.Response(200, json={"ok": True})

        pools = {
            "grok": {
                "port": 8318,
                "upstreams": [
                    Upstream("slow", "grok", "http://mock/v1", "key", aliases=["grok-4.5"]),
                    Upstream("fast", "grok", "http://mock/v1", "key", aliases=["grok-4.5"]),
                ],
            }
        }
        app = build_app(pools)
        app.state.router.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})

        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert len(calls) == 2

    asyncio.run(_run())


def test_router_streaming_pass_through() -> None:
    async def _run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"data: hello\n\ndata: world\n\n",
            )

        pools = {
            "grok": {
                "port": 8318,
                "upstreams": [
                    Upstream(
                        "stream", "grok", "http://mock/v1", "key", aliases=["grok-4.5"]
                    )
                ],
            }
        }
        app = build_app(pools)
        app.state.router.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})

        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        body = await r.aread()
        assert b"data: hello" in body
        assert b"data: world" in body

    asyncio.run(_run())


def test_router_status_endpoint() -> None:
    async def _run() -> None:
        pools = {
            "grok": {
                "port": 8318,
                "upstreams": [
                    Upstream("ok", "grok", "http://mock/v1", "key", aliases=["grok-4.5"])
                ],
            }
        }
        app = build_app(pools)

        async with _test_client(app) as c:
            r = await c.get("/router/status")

        assert r.status_code == 200
        data = r.json()
        assert "ok" in data
        assert data["ok"]["pool"] == "grok"
        assert data["ok"]["aliases"] == ["grok-4.5"]

    asyncio.run(_run())


def test_load_pools_discovers_grok() -> None:
    pools = load_pools("D:/cli-proxy-api")
    assert "grok" in pools
    assert pools["grok"]["port"] == 8318
    upstreams = pools["grok"]["upstreams"]
    aliases = {alias for u in upstreams for alias in u.aliases}
    assert "grok-4.5" in aliases
    assert "*" in aliases  # local CLIProxy catch-all upstream
    assert not any(
        alias.startswith("remote-") for u in upstreams for alias in u.aliases
    )
    # Local is direct; at least one remote should carry Clash proxy_url when
    # config has per-key proxy-url (path consistency for probe + request).
    local = next(u for u in upstreams if "local-cliproxy" in u.name)
    assert not local.proxy_url
    remotes = [u for u in upstreams if "local-cliproxy" not in u.name]
    if remotes:
        assert any(u.proxy_url for u in remotes)


def test_client_for_uses_proxy_url() -> None:
    async def _run() -> None:
        router = SmartRouter("D:/cli-proxy-api")
        await router.setup()
        try:
            direct = Upstream(
                "d", "grok", "http://127.0.0.1:9/v1", "k", aliases=["*"], proxy_url=None
            )
            proxied = Upstream(
                "p",
                "grok",
                "http://remote/v1",
                "k",
                aliases=["grok-4.5"],
                proxy_url="http://127.0.0.1:7897",
            )
            c1 = router._client_for(direct)
            c2 = router._client_for(proxied)
            c3 = router._client_for(proxied)
            assert c1 is router.client
            assert c2 is not router.client
            assert c2 is c3  # shared per proxy endpoint
            assert getattr(router.client, "trust_env", True) is False
        finally:
            await router.close()

    asyncio.run(_run())


def test_proxy_request_uses_proxy_client() -> None:
    """Proxied upstreams must hit the proxy client, not the direct client."""

    async def _run() -> None:
        seen: list[str] = []

        def proxy_handler(request: httpx.Request) -> httpx.Response:
            seen.append("proxy")
            return httpx.Response(200, json={"via": "proxy"})

        def direct_handler(request: httpx.Request) -> httpx.Response:
            seen.append("direct")
            return httpx.Response(200, json={"via": "direct"})

        proxied = Upstream(
            "p",
            "grok",
            "http://mock/v1",
            "key",
            aliases=["grok-4.5"],
            proxy_url="http://proxy.local:1",
        )
        pools = {"grok": {"port": 8318, "upstreams": [proxied]}}
        app = build_app(pools)
        router: SmartRouter = app.state.router
        router.client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
        # Seed proxy client map with mock transport (same key as proxy_url).
        router._proxy_clients["http://proxy.local:1"] = httpx.AsyncClient(
            transport=httpx.MockTransport(proxy_handler)
        )

        async with _test_client(app) as c:
            r = await c.post("/v1/chat/completions", json={"model": "grok-4.5"})

        assert r.status_code == 200
        assert r.json() == {"via": "proxy"}
        assert seen == ["proxy"]

    asyncio.run(_run())


def test_smart_router_uses_config_dir() -> None:
    router = SmartRouter("D:/cli-proxy-api")
    assert router.config_dir == "D:/cli-proxy-api"
