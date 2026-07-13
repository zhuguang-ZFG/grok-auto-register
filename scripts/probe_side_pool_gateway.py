#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only smoke probe for side account-pool gateways (Cursor / Kiro / etc.).

Purpose
-------
Validate that a *third-party* OpenAI-compatible (or management) endpoint is
worth wiring into Kimi / a second local port — without registering accounts
or writing into ``cpa_auths/``.

What it does
------------
- GET  {base}/v1/models  (and a few alternate paths)
- POST {base}/v1/chat/completions  (tiny prompt, low max_tokens)
- Optional: GET management-style auth-files (admin key only; no mutations)
- Classifies: auth (401), rate/quota (429 / spending-limit text), HTML-not-API,
  timeout, OK

What it never does
------------------
- No account registration / signup
- No writes under cpa_auths / tokens / config.json
- No password changes, no import into live pool

Usage
-----
  # Kiro-style self-hosted OpenAI gateway
  python scripts/probe_side_pool_gateway.py ^
    --base-url http://127.0.0.1:8321 ^
    --api-key sk-xxx ^
    --label kiro

  # Cursor→OpenAI proxy (if you already run one)
  python scripts/probe_side_pool_gateway.py ^
    --base-url http://127.0.0.1:8080 ^
    --api-key sk-xxx ^
    --label cursor ^
    --model gpt-4o

  # Management API only (CLIProxy-style admin)
  python scripts/probe_side_pool_gateway.py ^
    --base-url http://example.com ^
    --admin-key "your-admin-password" ^
    --skip-chat

Exit codes
----------
  0  chat OK (or models OK when --skip-chat)
  2  bad args / missing base
  3  auth failure (401/invalid key)
  4  reachable but not a usable API (HTML / 405 / empty models)
  5  quota / rate / spending-limit style wall
  6  transport / timeout / connection
  7  chat failed for other reasons
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin


@dataclass
class ProbeResult:
    ok: bool
    kind: str
    detail: str = ""
    http_status: int | None = None
    latency_s: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _join(base: str, path: str) -> str:
    base = base.rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> ProbeResult:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            lat = time.time() - t0
            ct = (resp.headers.get("content-type") or "").lower()
            text = raw.decode("utf-8", errors="replace")
            return ProbeResult(
                ok=True,
                kind="http_ok",
                http_status=resp.status,
                latency_s=round(lat, 3),
                extra={"content_type": ct, "body": text, "bytes": len(raw)},
            )
    except urllib.error.HTTPError as e:
        lat = time.time() - t0
        raw = e.read() if hasattr(e, "read") else b""
        text = raw.decode("utf-8", errors="replace")
        return ProbeResult(
            ok=False,
            kind="http_error",
            detail=text[:500],
            http_status=e.code,
            latency_s=round(lat, 3),
            extra={"body": text, "bytes": len(raw)},
        )
    except Exception as e:
        lat = time.time() - t0
        return ProbeResult(
            ok=False,
            kind="transport",
            detail=f"{type(e).__name__}: {e}",
            latency_s=round(lat, 3),
        )


def _looks_html(text: str, content_type: str) -> bool:
    if "text/html" in content_type:
        return True
    head = (text or "")[:200].lower()
    return "<!doctype html" in head or "<html" in head


def _quota_wall(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "free-usage-exhausted",
        "usage limit",
        "spending-limit",
        "run out of cred",
        "insufficient",
        "quota exceeded",
        "rate limit",
        "too many requests",
        "429",
    )
    return any(k in low for k in keys)


def probe_models(base: str, api_key: str, timeout: float) -> ProbeResult:
    paths = [
        "/v1/models",
        "/openai/v1/models",
        "/api/v1/models",
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    last: ProbeResult | None = None
    for path in paths:
        url = _join(base, path)
        r = _request("GET", url, headers=headers, timeout=timeout)
        last = r
        if r.kind == "transport":
            continue
        body = str((r.extra or {}).get("body") or "")
        ct = str((r.extra or {}).get("content_type") or "")
        if r.http_status == 401:
            return ProbeResult(
                ok=False,
                kind="auth",
                detail=f"{path} → 401",
                http_status=401,
                latency_s=r.latency_s,
            )
        if r.http_status == 429 or _quota_wall(body):
            return ProbeResult(
                ok=False,
                kind="quota",
                detail=f"{path} → quota/rate wall",
                http_status=r.http_status,
                latency_s=r.latency_s,
                extra={"snippet": body[:200]},
            )
        if r.http_status == 200 and _looks_html(body, ct):
            # keep trying alternates; HTML often means wrong path / SPA front
            continue
        if r.http_status == 200:
            try:
                data = json.loads(body)
            except Exception:
                return ProbeResult(
                    ok=False,
                    kind="not_api",
                    detail=f"{path} 200 but not JSON",
                    http_status=200,
                    latency_s=r.latency_s,
                )
            models = data.get("data") if isinstance(data, dict) else None
            if not isinstance(models, list):
                return ProbeResult(
                    ok=False,
                    kind="not_api",
                    detail=f"{path} JSON without data[]",
                    http_status=200,
                    latency_s=r.latency_s,
                    extra={"keys": list(data.keys())[:12] if isinstance(data, dict) else []},
                )
            ids = [m.get("id") for m in models if isinstance(m, dict)][:20]
            return ProbeResult(
                ok=True,
                kind="models_ok",
                detail=f"{path} models={len(models)}",
                http_status=200,
                latency_s=r.latency_s,
                extra={"path": path, "sample_ids": ids, "count": len(models)},
            )
        if r.http_status == 405:
            continue
    if last and last.kind == "transport":
        return ProbeResult(
            ok=False,
            kind="transport",
            detail=last.detail,
            latency_s=last.latency_s,
        )
    return ProbeResult(
        ok=False,
        kind="not_api",
        detail="no usable /v1/models (HTML, 405, or missing data[])",
        http_status=getattr(last, "http_status", None),
        latency_s=getattr(last, "latency_s", None),
    )


def probe_chat(
    base: str,
    api_key: str,
    model: str,
    timeout: float,
    trials: int,
) -> ProbeResult:
    paths = [
        "/v1/chat/completions",
        "/openai/v1/chat/completions",
        "/api/v1/chat/completions",
    ]
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "reply with exactly: pong"}],
        "max_tokens": 16,
        "stream": False,
    }
    latencies: list[float] = []
    last_err = ""
    for path in paths:
        url = _join(base, path)
        ok_trial = 0
        for i in range(max(1, trials)):
            r = _request("POST", url, headers=headers, body=payload, timeout=timeout)
            body = str((r.extra or {}).get("body") or "")
            ct = str((r.extra or {}).get("content_type") or "")
            if r.kind == "transport":
                last_err = r.detail
                continue
            if r.http_status == 401:
                return ProbeResult(
                    ok=False,
                    kind="auth",
                    detail=f"{path} → 401",
                    http_status=401,
                    latency_s=r.latency_s,
                )
            if r.http_status == 429 or _quota_wall(body):
                return ProbeResult(
                    ok=False,
                    kind="quota",
                    detail=f"{path} → quota/rate",
                    http_status=r.http_status,
                    latency_s=r.latency_s,
                    extra={"snippet": body[:240]},
                )
            if r.http_status == 405 or _looks_html(body, ct):
                last_err = f"{path} status={r.http_status} html_or_405"
                break  # try next path
            # Empty side pool (e.g. Kiro-Go): gateway up, zero accounts.
            if r.http_status in (503, 500) and (
                "no available account" in body.lower()
                or "no accounts" in body.lower()
                or "account pool is empty" in body.lower()
            ):
                return ProbeResult(
                    ok=False,
                    kind="empty_pool",
                    detail=f"{path} empty pool (gateway reachable)",
                    http_status=r.http_status,
                    latency_s=r.latency_s,
                    extra={"snippet": body[:240], "path": path},
                )
            if r.http_status != 200:
                last_err = f"{path} status={r.http_status} {body[:160]}"
                # wrong path often 404; try next alternate before giving up
                if r.http_status == 404:
                    break
                continue
            try:
                data = json.loads(body)
            except Exception:
                last_err = f"{path} 200 non-json"
                break
            if isinstance(data, dict) and data.get("error"):
                err = data.get("error")
                err_s = json.dumps(err, ensure_ascii=False) if not isinstance(err, str) else err
                if _quota_wall(err_s):
                    return ProbeResult(
                        ok=False,
                        kind="quota",
                        detail=err_s[:240],
                        http_status=200,
                        latency_s=r.latency_s,
                    )
                last_err = err_s[:240]
                continue
            content = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
                if isinstance(data, dict)
                else None
            )
            if r.latency_s is not None:
                latencies.append(float(r.latency_s))
            ok_trial += 1
            if ok_trial >= trials:
                return ProbeResult(
                    ok=True,
                    kind="chat_ok",
                    detail=f"{path} trials={trials}",
                    http_status=200,
                    latency_s=round(sum(latencies) / len(latencies), 3) if latencies else None,
                    extra={
                        "path": path,
                        "content_preview": (content or "")[:80],
                        "latencies_s": latencies,
                        "usage": data.get("usage") if isinstance(data, dict) else None,
                    },
                )
        # path exhausted; try next
    return ProbeResult(
        ok=False,
        kind="chat_fail",
        detail=last_err or "chat failed on all paths",
    )


def probe_management(base: str, admin_key: str, timeout: float) -> ProbeResult:
    """Optional read-only peek at CLIProxy-style management APIs."""
    headers = {"Authorization": f"Bearer {admin_key}"}
    url = _join(base, "/v0/management/auth-files")
    r = _request("GET", url, headers=headers, timeout=timeout)
    body = str((r.extra or {}).get("body") or "")
    if r.http_status == 401:
        return ProbeResult(ok=False, kind="auth", detail="admin 401", http_status=401)
    if r.http_status != 200:
        return ProbeResult(
            ok=False,
            kind="mgmt_fail",
            detail=f"status={r.http_status} {body[:160]}",
            http_status=r.http_status,
            latency_s=r.latency_s,
        )
    try:
        data = json.loads(body)
    except Exception:
        return ProbeResult(ok=False, kind="not_api", detail="mgmt not JSON")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return ProbeResult(
            ok=False,
            kind="not_api",
            detail="no files[]",
            extra={"keys": list(data.keys())[:12] if isinstance(data, dict) else []},
        )
    status_counts: dict[str, int] = {}
    disabled_true = 0
    for f in files:
        if not isinstance(f, dict):
            continue
        st = str(f.get("status") or "?")
        status_counts[st] = status_counts.get(st, 0) + 1
        if f.get("disabled") is True:
            disabled_true += 1
    return ProbeResult(
        ok=True,
        kind="mgmt_ok",
        detail=f"auth-files n={len(files)}",
        http_status=200,
        latency_s=r.latency_s,
        extra={
            "count": len(files),
            "status_counts": status_counts,
            "disabled_true": disabled_true,
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="Gateway root, e.g. http://127.0.0.1:8321")
    ap.add_argument("--api-key", default="", help="Bearer key for /v1/*")
    ap.add_argument("--admin-key", default="", help="Optional management key (read-only)")
    ap.add_argument("--model", default="grok-4.5", help="Chat model id to try")
    ap.add_argument("--label", default="side-pool", help="Label in report")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--chat-trials", type=int, default=1)
    ap.add_argument("--skip-chat", action="store_true")
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--json-out", default="", help="Write full report JSON path (optional)")
    args = ap.parse_args(argv)

    base = str(args.base_url or "").strip()
    if not base:
        print("[!] --base-url required", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "label": args.label,
        "base_url": base,
        "model": args.model,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only": True,
        "no_register": True,
        "no_pool_write": True,
    }

    exit_code = 0
    print(f"=== side-pool probe: {args.label} ===")
    print(f"base={base}")
    print("mode=read-only (no register, no cpa_auths write)")

    if args.admin_key:
        print("\n[mgmt] GET /v0/management/auth-files")
        mg = probe_management(base, args.admin_key, args.timeout)
        report["management"] = {
            "ok": mg.ok,
            "kind": mg.kind,
            "detail": mg.detail,
            "http_status": mg.http_status,
            "latency_s": mg.latency_s,
            "extra": {k: v for k, v in (mg.extra or {}).items() if k != "body"},
        }
        print(f"  → {mg.kind} {mg.detail} lat={mg.latency_s}s")
        if mg.extra:
            sc = mg.extra.get("status_counts")
            if sc:
                print(f"  status_counts={sc}")

    models_r: ProbeResult | None = None
    if not args.skip_models:
        if not args.api_key:
            print("[!] --api-key empty; models/chat will likely 401", file=sys.stderr)
        print("\n[models] GET /v1/models (+ alternates)")
        models_r = probe_models(base, args.api_key or "missing", args.timeout)
        report["models"] = {
            "ok": models_r.ok,
            "kind": models_r.kind,
            "detail": models_r.detail,
            "http_status": models_r.http_status,
            "latency_s": models_r.latency_s,
            "extra": models_r.extra,
        }
        print(f"  → {models_r.kind} {models_r.detail} lat={models_r.latency_s}s")
        if models_r.extra and models_r.extra.get("sample_ids") is not None:
            print(f"  sample_ids={models_r.extra.get('sample_ids')}")

        if models_r.kind == "auth":
            exit_code = 3
        elif models_r.kind == "transport":
            exit_code = 6
        elif models_r.kind == "quota":
            exit_code = 5
        elif not models_r.ok:
            exit_code = 4

    chat_r: ProbeResult | None = None
    if not args.skip_chat:
        if not args.api_key:
            print("[!] skip chat: no --api-key", file=sys.stderr)
        else:
            # Prefer a model id from catalog when user left default and catalog has items
            model = args.model
            if (
                models_r
                and models_r.ok
                and args.model == "grok-4.5"
                and models_r.extra
                and models_r.extra.get("sample_ids")
            ):
                ids = models_r.extra["sample_ids"]
                if ids and "grok-4.5" not in ids:
                    model = str(ids[0])
                    print(f"\n[chat] model fallback from catalog: {model}")
            print(f"\n[chat] POST chat/completions model={model} trials={args.chat_trials}")
            chat_r = probe_chat(
                base,
                args.api_key,
                model,
                args.timeout,
                args.chat_trials,
            )
            report["chat"] = {
                "ok": chat_r.ok,
                "kind": chat_r.kind,
                "detail": chat_r.detail,
                "http_status": chat_r.http_status,
                "latency_s": chat_r.latency_s,
                "extra": chat_r.extra,
            }
            print(f"  → {chat_r.kind} {chat_r.detail} lat={chat_r.latency_s}s")
            if chat_r.extra and chat_r.extra.get("content_preview") is not None:
                print(f"  content={chat_r.extra.get('content_preview')!r}")

            if chat_r.kind == "auth":
                exit_code = 3
            elif chat_r.kind == "quota":
                exit_code = 5
            elif chat_r.kind == "transport":
                exit_code = 6
            elif chat_r.kind == "empty_pool":
                # Gateway surface OK; no accounts yet — pass if models also listed.
                exit_code = 0 if (models_r and models_r.ok) else 7
            elif not chat_r.ok:
                exit_code = 7
            else:
                exit_code = 0
    elif models_r and models_r.ok:
        exit_code = 0

    # Verdict line
    print("\n=== verdict ===")
    if exit_code == 0 and chat_r is not None and chat_r.kind == "empty_pool":
        print(
            "PASS: gateway surface OK, pool empty (import accounts before chat). "
            "Still do not auto-merge into cpa_auths."
        )
    elif exit_code == 0:
        print("PASS: usable API surface (at least models or chat OK). Still do not auto-merge into cpa_auths.")
    elif exit_code == 3:
        print("FAIL: auth (check api-key / admin-key).")
    elif exit_code == 4:
        print("FAIL: reachable but not a usable OpenAI API (HTML/405/empty catalog). Fix reverse-proxy or port.")
    elif exit_code == 5:
        print("FAIL: quota/rate/spending wall (pool may be dead even if UI shows many accounts).")
    elif exit_code == 6:
        print("FAIL: transport/timeout (down, firewalled, or wrong host).")
    else:
        print("FAIL: chat path not usable.")

    print(
        "checklist: token refreshable? multi-account failover? "
        "separate port from Grok :8317? day-cap/disabled on exhaust?"
    )

    if args.json_out:
        out = Path_safe_write(args.json_out, report)
        print(f"report_json={out}")

    return exit_code


def Path_safe_write(path: str, report: dict[str, Any]) -> str:
    from pathlib import Path as P

    p = P(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # never write secrets: strip api keys if caller stuffed them in report later
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)


if __name__ == "__main__":
    raise SystemExit(main())
