#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K12 mother-account invite flow (C) — community-aligned.

Community truth:
  - Child request into foreign K12 workspace fails with:
      "Only users with emails on the same domain can request access"
  - Child accept may return HTTP 200 but still NOT join (false success)
  - Working path requires mother/admin invite power:

      Mother:
        POST /backend-api/accounts/{workspace_id}/invites
        body: {"emails": ["child@..."]}   # field name may vary by build

      Child:
        POST /backend-api/accounts/{workspace_id}/invites/accept
        then re-login / workspace select
        verify plan_type == k12 via accounts/check

This script implements that pipeline and refuses the known-false path of
"hotmail free request into shared workspace".

Mother auth sources (any one):
  1) --mother-session path/to/session.json   (chatgpt /api/auth/session dump)
  2) --mother-token <access_token>
  3) env K12_MOTHER_ACCESS_TOKEN

Examples:
  # dry-run invite plan
  python scripts/k12_mother_invite.py plan --workspace fc4f... --emails a@x.com,b@y.com

  # invite with mother session
  python scripts/k12_mother_invite.py invite --mother-session mother.json --workspace fc4f... --emails-file kids.txt

  # child accept + verify
  python scripts/k12_mother_invite.py accept --child-token <AT> --workspace fc4f...

  # full: invite then accept+verify for registered free accounts file
  python scripts/k12_mother_invite.py run --mother-session mother.json --workspace fc4f... --children children.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_WS = "fc4f8db5-72cd-44cb-ae0d-fef1370a16c8"  # from 80500 export
DEFAULT_PROXY = "http://127.0.0.1:7897"
API = "https://chatgpt.com/backend-api"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mother_token_from_args(args: argparse.Namespace) -> str:
    if args.mother_token:
        return args.mother_token.strip()
    if args.mother_session:
        data = load_json(args.mother_session)
        tok = data.get("accessToken") or data.get("access_token") or ""
        if not tok:
            raise SystemExit("mother session missing accessToken")
        return str(tok)
    env = os.environ.get("K12_MOTHER_ACCESS_TOKEN", "").strip()
    if env:
        return env
    raise SystemExit("need --mother-token or --mother-session or env K12_MOTHER_ACCESS_TOKEN")


def session(proxy: str):
    from curl_cffi import requests as cffi_requests

    s = cffi_requests.Session(impersonate="chrome")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def headers(token: str, device_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "oai-device-id": device_id or str(uuid.uuid4()),
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
    }


def check_plan(s, token: str, proxy: str = "") -> dict[str, Any]:
    r = s.get(
        f"{API}/accounts/check/v4-2023-04-27",
        headers=headers(token),
        timeout=30,
    )
    out: dict[str, Any] = {"http": r.status_code}
    if r.status_code != 200:
        out["error"] = r.text[:200]
        return out
    data = r.json()
    accounts = data.get("accounts") or {}
    plans = []
    if isinstance(accounts, dict):
        for k, v in accounts.items():
            if k == "default":
                continue
            acc = (v or {}).get("account") or v or {}
            plans.append(
                {
                    "account_id": acc.get("account_id") or k,
                    "plan_type": acc.get("plan_type"),
                    "structure": acc.get("structure"),
                }
            )
    out["plans"] = plans
    out["is_k12"] = any(str(p.get("plan_type") or "").lower() in {"k12", "education", "edu"} for p in plans)
    return out


def parse_emails(args: argparse.Namespace) -> list[str]:
    emails: list[str] = []
    if args.emails:
        for part in args.emails.split(","):
            e = part.strip()
            if e:
                emails.append(e)
    if args.emails_file:
        for line in Path(args.emails_file).read_text(encoding="utf-8").splitlines():
            e = line.strip()
            if e and not e.startswith("#"):
                emails.append(e.split(",")[0].strip())
    # unique preserve order
    seen = set()
    out = []
    for e in emails:
        el = e.lower()
        if el in seen:
            continue
        seen.add(el)
        out.append(e)
    return out


def invite_one(s, mother_token: str, workspace: str, email: str) -> dict[str, Any]:
    """Try community-known invite body shapes."""
    url = f"{API}/accounts/{workspace}/invites"
    bodies = [
        {"emails": [email]},
        {"email_addresses": [email]},
        {"email": email},
        {"invitees": [{"email": email}]},
    ]
    last = {"ok": False, "error": "no body worked"}
    for body in bodies:
        r = s.post(url, headers=headers(mother_token), json=body, timeout=30)
        if r.status_code in (200, 201, 204):
            return {"ok": True, "http": r.status_code, "body": r.text[:300], "payload": body}
        last = {"ok": False, "http": r.status_code, "error": r.text[:300], "payload": body}
        # 400 on shape -> try next; 401/403 means mother lacks permission
        if r.status_code in (401, 403):
            return last
    return last


def accept_one(s, child_token: str, workspace: str) -> dict[str, Any]:
    url = f"{API}/accounts/{workspace}/invites/accept"
    r = s.post(url, headers=headers(child_token), json={}, timeout=30)
    # NOTE: community + our tests show 200 can be false success
    return {"http": r.status_code, "body": r.text[:300], "ok_http": r.status_code in (200, 201, 204)}


def cmd_plan(args: argparse.Namespace) -> int:
    emails = parse_emails(args)
    log("=== K12 mother-invite plan ===")
    log(f"workspace: {args.workspace}")
    log(f"emails: {len(emails)}")
    for e in emails[:20]:
        log(f"  - {e}")
    if len(emails) > 20:
        log(f"  ... +{len(emails)-20} more")
    log("")
    log("Required:")
    log("  1) mother/admin access_token for this workspace")
    log("  2) mother POST /invites with child emails")
    log("  3) child POST /invites/accept")
    log("  4) verify child plan_type == k12 (do NOT trust accept 200 alone)")
    log("")
    log("Will NOT work:")
    log("  - hotmail free child request into foreign K12 (same-domain 401)")
    log("  - accept without prior mother invite (false success risk)")
    return 0


def cmd_invite(args: argparse.Namespace) -> int:
    emails = parse_emails(args)
    if not emails:
        raise SystemExit("no emails")
    mother = mother_token_from_args(args)
    s = session(args.proxy)
    log(f"mother invite -> workspace {args.workspace}, n={len(emails)}")
    # mother self-check
    mcheck = check_plan(s, mother, args.proxy)
    log(f"mother check: {mcheck}")
    if mcheck.get("http") != 200:
        log("WARN: mother token cannot call check API; invite may still work if scopes differ")

    ok = fail = 0
    results = []
    for i, email in enumerate(emails, 1):
        res = invite_one(s, mother, args.workspace, email)
        results.append({"email": email, **res})
        if res.get("ok"):
            ok += 1
            log(f"  [{i}/{len(emails)}] INVITED {email}")
        else:
            fail += 1
            log(f"  [{i}/{len(emails)}] FAIL {email} http={res.get('http')} {res.get('error')}")
        time.sleep(float(args.sleep))
    out = Path(args.out or "data/k12_invite_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"done ok={ok} fail={fail} saved {out}")
    return 0 if fail == 0 else 1


def cmd_accept(args: argparse.Namespace) -> int:
    child = (args.child_token or "").strip()
    if not child and args.child_session:
        data = load_json(args.child_session)
        child = data.get("accessToken") or data.get("access_token") or ""
    if not child:
        raise SystemExit("need --child-token or --child-session")
    s = session(args.proxy)
    before = check_plan(s, child, args.proxy)
    log(f"before: {before}")
    acc = accept_one(s, child, args.workspace)
    log(f"accept: {acc}")
    # hard verify
    time.sleep(2)
    after = check_plan(s, child, args.proxy)
    log(f"after: {after}")
    if after.get("is_k12"):
        log("SUCCESS: child is K12")
        return 0
    log("FALSE SUCCESS RISK: accept http ok but plan_type is not k12")
    return 2


def load_children(path: Path) -> list[dict[str, str]]:
    """JSONL lines: {email, access_token, refresh_token?} or JSON list."""
    text = path.read_text(encoding="utf-8")
    kids: list[dict[str, str]] = []
    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                kids.append(obj)
        return kids
    obj = json.loads(text)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict) and isinstance(obj.get("accounts"), list):
        return [x for x in obj["accounts"] if isinstance(x, dict)]
    raise SystemExit("unsupported children file format")


def cmd_run(args: argparse.Namespace) -> int:
    mother = mother_token_from_args(args)
    children = load_children(Path(args.children))
    s = session(args.proxy)
    log(f"run invite+accept+verify n={len(children)} workspace={args.workspace}")

    mcheck = check_plan(s, mother, args.proxy)
    log(f"mother check: {mcheck}")

    ok = false_success = fail = 0
    report = []
    for i, child in enumerate(children, 1):
        email = str(child.get("email") or "")
        ctok = str(child.get("access_token") or child.get("accessToken") or "")
        if not email or not ctok:
            fail += 1
            report.append({"email": email, "error": "missing email/token"})
            continue
        inv = invite_one(s, mother, args.workspace, email)
        if not inv.get("ok"):
            fail += 1
            log(f"  [{i}] invite FAIL {email} {inv}")
            report.append({"email": email, "invite": inv, "ok": False})
            continue
        time.sleep(1)
        acc = accept_one(s, ctok, args.workspace)
        time.sleep(2)
        after = check_plan(s, ctok, args.proxy)
        is_k12 = bool(after.get("is_k12"))
        if is_k12:
            ok += 1
            log(f"  [{i}] OK K12 {email}")
        else:
            false_success += 1
            log(f"  [{i}] FALSE SUCCESS {email} accept={acc.get('http')} plans={after.get('plans')}")
        report.append({"email": email, "invite": inv, "accept": acc, "after": after, "ok": is_k12})
        time.sleep(float(args.sleep))

    out = Path(args.out or "data/k12_mother_run_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"done ok={ok} false_success={false_success} fail={fail} saved {out}")
    return 0 if false_success == 0 and fail == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="K12 mother invite pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--workspace", default=DEFAULT_WS)
        sp.add_argument("--proxy", default=DEFAULT_PROXY)
        sp.add_argument("--sleep", type=float, default=0.5)

    p_plan = sub.add_parser("plan")
    add_common(p_plan)
    p_plan.add_argument("--emails", default="")
    p_plan.add_argument("--emails-file", default="")

    p_inv = sub.add_parser("invite")
    add_common(p_inv)
    p_inv.add_argument("--mother-token", default="")
    p_inv.add_argument("--mother-session", default="")
    p_inv.add_argument("--emails", default="")
    p_inv.add_argument("--emails-file", default="")
    p_inv.add_argument("--out", default="")

    p_acc = sub.add_parser("accept")
    add_common(p_acc)
    p_acc.add_argument("--child-token", default="")
    p_acc.add_argument("--child-session", default="")

    p_run = sub.add_parser("run")
    add_common(p_run)
    p_run.add_argument("--mother-token", default="")
    p_run.add_argument("--mother-session", default="")
    p_run.add_argument("--children", required=True, help="json/jsonl with child tokens")
    p_run.add_argument("--out", default="")

    args = p.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "invite":
        return cmd_invite(args)
    if args.cmd == "accept":
        return cmd_accept(args)
    if args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
