#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end register one Databricks trial account."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from . import browser_signup, email_bridge, fake_identity, pool, probe, token_mint
from .config import get_databricks_section
from .schema import new_credential

LogFn = Callable[[str], None]


def register_one(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    log: Optional[LogFn] = None,
) -> Dict[str, Any]:
    """Run full pipeline once; always persists a credential JSON."""
    cfg = cfg or get_databricks_section()
    log = log or (lambda m: print(m, flush=True))

    if not pool.can_register_more(cfg, 1):
        raise RuntimeError(
            f"daily cap reached ({pool.get_daily_count(cfg)}/{cfg.get('max_per_day')})"
        )

    identity = fake_identity.make_identity()
    email, secret, provider = email_bridge.create_mailbox(cfg, log=log)
    password = identity["password"]

    cred = new_credential(
        email=email,
        password=password,
        status="incomplete",
    )
    pool.save_credential(cred, cfg)

    def verify_cb():
        return email_bridge.wait_verification(
            email,
            secret,
            provider,
            cfg=cfg,
            timeout=float(cfg.get("otp_timeout_sec") or 180),
            log=log,
        )

    br = browser_signup.run_signup(
        email,
        password,
        identity,
        cfg=cfg,
        log=log,
        verify_callback=verify_cb,
    )
    page = br.get("page")
    try:
        if br.get("status") == "needs_human":
            cred["status"] = "needs_human"
            cred["needs_human_detail"] = br.get("detail")
            cred["disable_reason"] = br.get("detail")
            pool.save_credential(cred, cfg)
            pool.incr_daily_count(cfg)
            return cred

        if br.get("status") != "workspace_ready" or not br.get("host"):
            cred["status"] = "incomplete"
            cred["disable_reason"] = br.get("detail") or "signup_failed"
            pool.save_credential(cred, cfg)
            pool.incr_daily_count(cfg)
            return cred

        host = str(br["host"])
        cred["host"] = host
        selectors = browser_signup.load_selectors(cfg)

        from .onboarding import try_skip_onboarding

        if page is not None:
            try_skip_onboarding(page, selectors, log=log)
            token, detail = token_mint.mint_pat(page, host, selectors, log=log)
        else:
            token, detail = None, "no_page"

        if not token:
            cred["status"] = "incomplete"
            cred["disable_reason"] = f"pat_failed:{detail}"
            pool.save_credential(cred, cfg)
            pool.incr_daily_count(cfg)
            return cred

        cred["token"] = token
        cred = probe.probe_credential(cred, cfg)
        pool.save_credential(cred, cfg)
        pool.incr_daily_count(cfg)
        log(f"[pipeline] done status={cred.get('status')} email={email}")
        return cred
    finally:
        if page is not None:
            try:
                page.quit()
            except Exception:
                pass


def register_many(
    count: int,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    log: Optional[LogFn] = None,
) -> list:
    cfg = cfg or get_databricks_section()
    log = log or (lambda m: print(m, flush=True))
    count = max(1, int(count))
    interval = float(cfg.get("min_interval_sec") or 120)
    results = []
    for i in range(count):
        if not pool.can_register_more(cfg, 1):
            log(f"[pipeline] stop: daily cap at {pool.get_daily_count(cfg)}")
            break
        log(f"[pipeline] account {i + 1}/{count}")
        try:
            results.append(register_one(cfg, log=log))
        except Exception as exc:
            log(f"[pipeline] error: {exc}")
            results.append({"error": str(exc)})
        if i + 1 < count:
            time.sleep(interval)
    return results
