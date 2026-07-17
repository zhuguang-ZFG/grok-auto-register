#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retest quarantined auths and move survivors back to live pool.

Community absorb (acpa_watchdog) + HARDEN (2026-07-18):
  - 403 permission-denied is soft. After recover_after expires, retest once.
    Chat OK -> live. Still 403 -> extend hold.
  - 429 / free-usage-exhausted / rate-limit is also soft (rolling 2M/24h quota).
    Extend hold — do NOT discard. Community: tokens free up over hours.
  - network / server / probe flakiness: extend hold (not terminal).
  - Hard discard only: 401 auth, anti-bot, missing access_token, refresh_revoked.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpa_xai.oauth_device import OAuthDeviceError, refresh_access_token
from cpa_xai.probe import probe_account_health
from cpa_xai.proxyutil import next_proxy_from_pool, proxy_log_label
from cpa_xai.quarantine import (
    DEFAULT_RECOVER_AFTER_SEC,
    discard_auth,
    iter_quarantined,
    move_to_live,
    update_hold,
)
from cpa_xai.raceguard import rt_rotated_by_other
from datetime import datetime, timedelta, timezone

# Soft holds: keep file, extend recover window (matches usage.py 6h default).
_SOFT_TAGS = frozenset({
    "permission-denied",
    "forbidden",
    "quota-exhausted",
    "quota_exhausted",
    "rate-limit",
    "rate_limit",
    "network",
    "network_error",
    "server",
    "probe_error",
    "models-fail",
    "error",
})
# Hard discard only when account is truly unusable.
_HARD_TAGS = frozenset({
    "auth",
    "anti-bot",
    "anti_bot",
    "refresh_revoked",
    "invalid_grant",
    "missing_access_token",
})


def load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT), help="project root")
    ap.add_argument(
        "--recover-after",
        type=float,
        default=float(DEFAULT_RECOVER_AFTER_SEC),
        help="seconds to hold after a failed retest",
    )
    ap.add_argument("--max-retests", type=int, default=3)
    ap.add_argument(
        "--max",
        type=int,
        default=0,
        help="max accounts to retest this run (0=all ready)",
    )
    ap.add_argument(
        "--quota-only",
        action="store_true",
        help="only retest quota_exhausted / free-usage soft holds",
    )
    args = ap.parse_args(argv)

    root = Path(args.root)
    cfg = load_cfg()
    proxy = next_proxy_from_pool(cfg)
    print(f"[*] retest proxy={proxy_log_label(proxy)} root={root} max={args.max or 'all'}")

    rescued = discarded = extended = skipped = 0
    processed = 0
    # Prefer models-only retest for bulk quota holds (chat burns free 2M window).
    for src, auth in iter_quarantined(root):
        if args.max and processed >= int(args.max):
            print(f"[*] hit --max={args.max}, stopping")
            break
        q = auth.get("_quarantine") or {}
        prior_reason = str(q.get("reason") or q.get("last_status") or "").lower()
        if args.quota_only and prior_reason not in (
            "quota_exhausted",
            "quota-exhausted",
            "free-usage-exhausted",
            "rate_limit",
            "rate-limit",
        ):
            continue
        processed += 1

        # Terminal quarantine reasons: discard without burning probes.
        if prior_reason in (
            "refresh_revoked",
            "invalid_grant",
            "missing_refresh_token",
            "missing_access_token",
            "anti-bot",
            "anti_bot",
        ):
            discard_auth(auth, root=root, reason=prior_reason or "terminal")
            src.unlink(missing_ok=True)
            discarded += 1
            print(f"[discard] {src.name}: terminal prior={prior_reason}")
            continue

        if int(q.get("retest_count", 0)) >= args.max_retests:
            # Soft holds that never recover after max retests: keep extended, don't discard
            # quota/403 (community: rolling recover). Only skip this round.
            print(f"[skip] {src.name}: max retests reached prior={prior_reason}")
            skipped += 1
            continue

        at = str(auth.get("access_token") or "").strip()
        rt = str(auth.get("refresh_token") or "").strip()
        if not at and not rt:
            print(f"[discard] {src.name}: no access_token/refresh_token")
            discard_auth(auth, root=root, reason="missing_access_token")
            src.unlink(missing_ok=True)
            discarded += 1
            continue

        # Always try refresh first when RT present — quarantine files often have
        # expired AT; models probe then falsely looks like permission-denied.
        if rt:
            tried_rt = rt
            try:
                tok = refresh_access_token(rt, proxy=proxy or None, retries=1)
                auth["access_token"] = tok.access_token
                at = tok.access_token
                if tok.refresh_token:
                    auth["refresh_token"] = tok.refresh_token
                if tok.expires_in:
                    exp = datetime.now(timezone.utc) + timedelta(seconds=int(tok.expires_in))
                    auth["expired"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
                    auth["expires_in"] = int(tok.expires_in)
                auth["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except OAuthDeviceError as e:
                err = str(e)
                if "invalid_grant" in err.lower() and rt_rotated_by_other(src, tried_rt):
                    # Another process rotated RT; re-read and continue with file AT.
                    try:
                        import json as _json

                        auth = _json.loads(src.read_text(encoding="utf-8"))
                        at = str(auth.get("access_token") or "").strip()
                    except Exception:
                        pass
                    print(f"[retest] {src.name}: RT rotated by other, reusing file AT")
                elif "invalid_grant" in err.lower():
                    discard_auth(auth, root=root, reason="refresh_revoked")
                    src.unlink(missing_ok=True)
                    discarded += 1
                    print(f"[discard] {src.name}: refresh_revoked")
                    continue
                else:
                    print(f"[retest] {src.name}: refresh soft-fail {err[:80]}")
            except Exception as e:  # noqa: BLE001
                print(f"[retest] {src.name}: refresh error {e}")

        if not at:
            update_hold(src, auth, recover_after_sec=args.recover_after, new_status="no_access_token")
            extended += 1
            continue

        # Soft holds (quota/403): models only — never burn free chat quota on retest.
        use_chat = False
        result = probe_account_health(
            at,
            base_url=str(auth.get("base_url") or "https://cli-chat-proxy.grok.com/v1"),
            proxy=proxy or None,
            probe_chat=use_chat,
        )
        tags = {str(t).lower().replace("_", "-") for t in (result.get("tags") or [])}
        # Normalize raw error text into tags when probe didn't classify.
        err_l = str(result.get("error") or "").lower()
        if "free-usage-exhausted" in err_l or "usage-exhausted" in err_l:
            tags.add("quota-exhausted")
        # Don't map generic "denied" / expired-credentials to permission-denied;
        # expired AT is auth, true chat gate is separate (we don't chat-probe here).
        if "permission-denied" in err_l or "chat endpoint is denied" in err_l:
            tags.add("permission-denied")
        if "invalid or expired credentials" in err_l or "expired credentials" in err_l:
            tags.add("auth")

        print(
            f"[retest] {src.name}: ok={result.get('ok')} chat_ok={result.get('chat_ok')} "
            f"tags={sorted(tags)} error={result.get('error', '')[:80]}"
        )

        # Success:
        # - models-only (quota holds): models OK → return to live (chat gate checked on real use)
        # - full probe: ok and chat not explicitly False
        models_ok = bool(result.get("ok"))
        chat_ok = result.get("chat_ok")
        if models_ok and (not use_chat or chat_ok is not False):
            move_to_live(src, auth, root=root)
            rescued += 1
            continue

        if tags & _HARD_TAGS:
            reason = sorted(tags & _HARD_TAGS)[0]
            discard_auth(auth, root=root, reason=reason)
            src.unlink(missing_ok=True)
            discarded += 1
            continue

        # Soft path (quota / 403 / network / unknown): extend hold, never discard.
        soft_status = (
            "quota_exhausted"
            if tags & {"quota-exhausted", "rate-limit"}
            else (
                "permission_denied"
                if tags & {"permission-denied", "forbidden"}
                else (sorted(tags)[0] if tags else prior_reason or "retest_fail")
            )
        )
        # Quota: shorter rolling window (6h); 403: full recover_after (24h default arg).
        hold_sec = (
            6 * 3600.0
            if soft_status == "quota_exhausted"
            else float(args.recover_after)
        )
        update_hold(src, auth, recover_after_sec=hold_sec, new_status=soft_status)
        extended += 1

    print(
        f"[*] rescued={rescued} discarded={discarded} extended={extended} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    # late json import to avoid top-level issues
    import json

    raise SystemExit(main())
