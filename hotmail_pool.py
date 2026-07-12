#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hotmail/Outlook account pool for Grok register (optional email provider).

Community pack line format::

    email----password----client_id_or_uuid----ms_refresh_token

OTP path (linux.do / grok-register peers):
  1) refresh_token → access_token (login.live.com / consumers oauth2)
  2) IMAP XOAUTH2 on outlook.office365.com:993
  3) parse xAI verification code from recent mail

This is **not** CPA / CLIProxy auth. Default register stays Cloudflare.
Enable with::

    \"email_provider\": \"hotmail\"

Pool file (gitignored): data/hotmail_pool.txt
Consumed: data/hotmail_pool.used.txt
Dead/revoked: data/hotmail_pool.dead.txt
"""
from __future__ import annotations

import argparse
import base64
import email
import imaplib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.header import decode_header
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
DEFAULT_POOL = ROOT / "data" / "hotmail_pool.txt"
DEFAULT_USED = ROOT / "data" / "hotmail_pool.used.txt"
DEFAULT_DEAD = ROOT / "data" / "hotmail_pool.dead.txt"
DEFAULT_STATE = ROOT / "data" / "hotmail_pool_state.json"

# Thunderbird / community fallbacks if line uuid is empty
FALLBACK_CLIENT_IDS = [
    "9e5f94bc-e8a4-4e73-b8be-63364c29d753",  # Thunderbird
    "d3590ed6-52b3-4102-aeff-aad2292ab01c",  # Microsoft Office
    "0000000048170EF2",  # older live SDK style (string)
]

TOKEN_URLS = [
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "https://login.live.com/oauth20_token.srf",
]

# scopes that work for IMAP XOAUTH2 on consumer hotmail
IMAP_SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access",
    "wl.imap offline_access",
    "https://graph.microsoft.com/Mail.Read offline_access",
]

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]
ResendFn = Callable[[], None]

_lock = threading.Lock()


def parse_line(line: str) -> dict[str, str] | None:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    parts = [p.strip() for p in raw.split("----")]
    if not parts or "@" not in parts[0]:
        return None
    return {
        "email": parts[0].lower(),
        "password": parts[1] if len(parts) > 1 else "",
        "client_id": parts[2] if len(parts) > 2 else "",
        "uuid": parts[2] if len(parts) > 2 else "",
        "refresh_token": parts[3] if len(parts) > 3 else "",
        "raw": raw,
        "raw_fields": str(len(parts)),
    }


def _pool_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or {}
    rel = str(cfg.get("hotmail_pool_path") or DEFAULT_POOL).strip()
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / p)


def _used_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or {}
    rel = str(cfg.get("hotmail_pool_used_path") or DEFAULT_USED).strip()
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / p)


def _dead_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or {}
    rel = str(cfg.get("hotmail_pool_dead_path") or DEFAULT_DEAD).strip()
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / p)


def load_pool(path: Path | str | None = None) -> list[dict[str, str]]:
    p = Path(path) if path else DEFAULT_POOL
    if not p.is_file():
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        row = parse_line(line)
        if not row:
            continue
        em = row["email"]
        if em in seen:
            continue
        seen.add(em)
        rows.append(row)
    return rows


def status(path: Path | str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Path(path) if path else _pool_path(cfg)
    rows = load_pool(p)
    with_rt = sum(1 for r in rows if r.get("refresh_token"))
    used_n = dead_n = 0
    up, dp = _used_path(cfg), _dead_path(cfg)
    if up.is_file():
        used_n = sum(1 for l in up.read_text(encoding="utf-8", errors="replace").splitlines() if "@" in l)
    if dp.is_file():
        dead_n = sum(1 for l in dp.read_text(encoding="utf-8", errors="replace").splitlines() if "@" in l)
    return {
        "path": str(p),
        "exists": p.is_file(),
        "unique": len(rows),
        "with_refresh_token": with_rt,
        "used_file_lines": used_n,
        "dead_file_lines": dead_n,
        "bytes": p.stat().st_size if p.is_file() else 0,
        "note": "optional email_provider=hotmail; default register is cloudflare",
    }


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def _rewrite_pool_without(path: Path, email: str) -> bool:
    """Remove email line from pool file. Returns True if removed."""
    if not path.is_file():
        return False
    email = (email or "").strip().lower()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept: list[str] = []
    removed = False
    for line in lines:
        row = parse_line(line)
        if row and row["email"] == email:
            removed = True
            continue
        kept.append(line)
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = "\n".join(kept) + ("\n" if kept else "")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    return removed


def pop_account(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Atomically take one account from the head of the pool → used file."""
    cfg = cfg or {}
    pool = _pool_path(cfg)
    used = _used_path(cfg)
    with _lock:
        if not pool.is_file():
            raise RuntimeError(f"hotmail pool missing: {pool}")
        lines = pool.read_text(encoding="utf-8", errors="replace").splitlines()
        chosen: dict[str, str] | None = None
        rest: list[str] = []
        for line in lines:
            if chosen is None:
                row = parse_line(line)
                if row and row.get("refresh_token"):
                    chosen = row
                    continue
            rest.append(line)
        if not chosen:
            raise RuntimeError("hotmail pool empty or no refresh_token lines")
        tmp = pool.with_suffix(pool.suffix + ".tmp")
        text = "\n".join(rest) + ("\n" if rest else "")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        # Windows can briefly lock replace target; retry
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                os.replace(tmp, pool)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        _append_line(used, chosen.get("raw") or chosen["email"])
        return chosen


def mark_dead(row: dict[str, str], cfg: dict[str, Any] | None = None, reason: str = "") -> None:
    cfg = cfg or {}
    line = row.get("raw") or row.get("email") or ""
    if reason:
        line = f"{line}  # {reason}"
    _append_line(_dead_path(cfg), line)


def _http_form(url: str, data: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "grok-auto-register/hotmail_pool",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"error": "http_error", "status": e.code, "body": raw[:300]}
    try:
        return json.loads(raw)
    except Exception:
        return {"error": "bad_json", "body": raw[:300]}


def refresh_access_token(
    row: dict[str, str],
    cfg: dict[str, Any] | None = None,
) -> str:
    """Exchange MS refresh_token for access_token. Tries client_id from line + fallbacks."""
    cfg = cfg or {}
    rt = (row.get("refresh_token") or "").strip()
    if not rt:
        raise RuntimeError("missing refresh_token")
    client_ids: list[str] = []
    cid = (row.get("client_id") or row.get("uuid") or "").strip()
    if cid:
        client_ids.append(cid)
    extra = str(cfg.get("hotmail_client_id") or "").strip()
    if extra:
        client_ids.append(extra)
    for c in FALLBACK_CLIENT_IDS:
        if c not in client_ids:
            client_ids.append(c)

    scopes = []
    cfg_scope = str(cfg.get("hotmail_oauth_scope") or "").strip()
    if cfg_scope:
        scopes.append(cfg_scope)
    scopes.extend(IMAP_SCOPES)

    last_err = ""
    for client_id in client_ids:
        for token_url in TOKEN_URLS:
            for scope in scopes:
                payload = {
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                    "scope": scope,
                }
                # some live endpoints also accept client_secret empty
                res = _http_form(token_url, payload)
                at = str(res.get("access_token") or "").strip()
                if at:
                    # persist rotated refresh_token if any
                    new_rt = str(res.get("refresh_token") or "").strip()
                    if new_rt:
                        row["refresh_token"] = new_rt
                    row["_access_token"] = at
                    row["_token_scope"] = scope
                    row["_token_client_id"] = client_id
                    return at
                err = res.get("error") or res.get("error_description") or res
                last_err = str(err)[:200]
    raise RuntimeError(f"ms token refresh failed: {last_err}")


def _xoauth2_raw(user: str, access_token: str) -> bytes:
    """SASL XOAUTH2 initial client response (raw, NOT base64).

    Python imaplib.authenticate() base64-encodes whatever the authobject returns.
    Pre-encoding causes: BAD Command Argument Error.
    """
    s = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return s.encode("utf-8")


def _decode_mime_header(val: str) -> str:
    if not val:
        return ""
    chunks = []
    for part, enc in decode_header(val):
        if isinstance(part, bytes):
            chunks.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            chunks.append(str(part))
    return " ".join(chunks)


_FALSE_CODE = {
    "app-img",
    "img-src",
    "pre-header",
    "web-view",
    "log-in",
    "sign-up",
    "sign-in",
    "x-ai",
}


def _looks_like_xai_mail(subject: str, text: str, from_addr: str = "") -> bool:
    blob = f"{subject}\n{from_addr}\n{text}".lower()
    keys = (
        "x.ai",
        "xai",
        "grok",
        "verification code",
        "verify your",
        "confirm your email",
        "your code is",
        "security code",
    )
    return any(k in blob for k in keys)


def _extract_code(text: str, subject: str = "", *, from_addr: str = "") -> str | None:
    """Extract xAI OTP; ignore Outlook welcome HTML false positives (e.g. app-img)."""
    subj = subject or ""
    body = text or ""

    # Strong: "ABC-123 xAI" subject
    m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subj, re.I)
    if m:
        return m.group(1).upper()

    # Only scan XXX-XXX / digit codes on mails that look like xAI/verify
    if not _looks_like_xai_mail(subj, body, from_addr):
        return None

    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", subj, re.I)
    if m:
        code = m.group(1)
        if code.lower() not in _FALSE_CODE:
            return code.upper()

    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", body, re.I)
    if m:
        code = m.group(1)
        if code.lower() not in _FALSE_CODE:
            return code.upper()

    for pat in (
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
        r"\bcode\s+is\s+(\d{4,8})\b",
    ):
        m = re.search(pat, body, re.I)
        if m:
            return m.group(1)
    return None


def imap_fetch_recent(
    email_addr: str,
    access_token: str,
    *,
    host: str = "outlook.office365.com",
    limit: int = 15,
) -> list[dict[str, str]]:
    """Fetch recent inbox messages via IMAP XOAUTH2."""
    raw_auth = _xoauth2_raw(email_addr, access_token)
    M = imaplib.IMAP4_SSL(host, 993)
    try:
        # authobject(response) -> bytes/str; imaplib applies base64
        typ, data = M.authenticate("XOAUTH2", lambda _r: raw_auth)
        if typ != "OK":
            raise RuntimeError(f"IMAP AUTH failed: {typ} {data}")
        M.select("INBOX")
        typ, data = M.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()
        ids = ids[-max(1, limit) :]
        out: list[dict[str, str]] = []
        for mid in reversed(ids):
            typ, msg_data = M.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw = part[1]
                    break
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            subject = _decode_mime_header(msg.get("Subject", ""))
            body_parts: list[str] = []
            if msg.is_multipart():
                for p in msg.walk():
                    ctype = p.get_content_type()
                    if ctype in ("text/plain", "text/html"):
                        try:
                            payload = p.get_payload(decode=True) or b""
                            charset = p.get_content_charset() or "utf-8"
                            body_parts.append(payload.decode(charset, errors="replace"))
                        except Exception:
                            pass
            else:
                try:
                    payload = msg.get_payload(decode=True) or b""
                    charset = msg.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    body_parts.append(str(msg.get_payload() or ""))
            text = "\n".join(body_parts)
            out.append(
                {
                    "id": mid.decode() if isinstance(mid, bytes) else str(mid),
                    "subject": subject,
                    "text": text,
                    "from": _decode_mime_header(msg.get("From", "")),
                }
            )
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def wait_code(
    dev_token: str,
    email: str = "",
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 180,
    poll_interval: float = 5.0,
    log: LogFn | None = None,
    cancel: CancelFn | None = None,
    resend: ResendFn | None = None,
) -> str:
    """Poll Hotmail IMAP until xAI verification code appears.

    ``dev_token`` is JSON blob of the account row (from pick_inbox) or raw refresh token.
    """
    cfg = cfg or {}

    def _log(msg: str) -> None:
        if log:
            log(msg)

    row: dict[str, str] = {}
    if str(dev_token or "").strip().startswith("{"):
        try:
            row = json.loads(dev_token)
        except Exception:
            row = {}
    if not row:
        row = {
            "email": (email or "").strip().lower(),
            "refresh_token": str(dev_token or "").strip(),
            "client_id": str(cfg.get("hotmail_client_id") or ""),
        }
    if not row.get("email") and email:
        row["email"] = email.strip().lower()
    if not row.get("email"):
        raise RuntimeError("hotmail wait_code: missing email")

    host = str(cfg.get("hotmail_imap_host") or "outlook.office365.com").strip()
    poll_interval = max(2.0, float(poll_interval or 5))
    deadline = time.time() + max(15.0, float(timeout or 180))
    resend_after = float(cfg.get("hotmail_resend_after_sec") or 45)
    started = time.time()
    resent = False
    seen_codes: set[str] = set()
    access = ""

    # initial token
    try:
        access = refresh_access_token(row, cfg)
        _log(f"[hotmail] token ok email={row['email']}")
    except Exception as exc:
        mark_dead(row, cfg, reason=f"token:{exc}")
        raise RuntimeError(f"hotmail token failed for {row['email']}: {exc}") from exc

    while time.time() < deadline:
        if cancel and cancel():
            raise RuntimeError("hotmail: cancelled")
        try:
            if not access:
                access = refresh_access_token(row, cfg)
            msgs = imap_fetch_recent(row["email"], access, host=host, limit=20)
        except Exception as exc:
            _log(f"[hotmail] imap error: {exc}")
            # force re-token next loop
            access = ""
            time.sleep(poll_interval)
            continue

        for m in msgs:
            subj = m.get("subject") or ""
            text = m.get("text") or ""
            frm = m.get("from") or ""
            code = _extract_code(text, subj, from_addr=frm)
            if not code:
                continue
            if code in seen_codes:
                continue
            # accept first *new* xAI-looking code
            _log(f"[hotmail] code={code!r} subject={subj[:60]!r}")
            return code

        if resend and not resent and (time.time() - started) >= resend_after:
            try:
                resend()
                resent = True
                _log("[hotmail] requested resend")
            except Exception as exc:
                _log(f"[hotmail] resend failed: {exc}")
        time.sleep(poll_interval)

    raise TimeoutError(f"hotmail: timeout waiting code for {row.get('email')}")


def pick_inbox(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Pop one hotmail account → (email, json_token_for_wait_code)."""
    cfg = cfg or {}
    # optional preflight: refresh token before returning (costs API; default on for first N? default True for quality)
    preflight = True
    v = cfg.get("hotmail_preflight_token", True)
    if isinstance(v, str):
        preflight = v.strip().lower() not in ("0", "false", "no", "off")
    else:
        preflight = bool(v)

    max_try = int(cfg.get("hotmail_pop_max_try") or 5)
    last_err = ""
    for _ in range(max(1, max_try)):
        row = pop_account(cfg)
        if not preflight:
            return row["email"], json.dumps(row, ensure_ascii=False)
        try:
            refresh_access_token(row, cfg)
            return row["email"], json.dumps(row, ensure_ascii=False)
        except Exception as exc:
            last_err = str(exc)
            mark_dead(row, cfg, reason=f"preflight:{exc}")
            continue
    raise RuntimeError(f"hotmail: no live account after {max_try} tries ({last_err})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hotmail pool status / smoke")
    ap.add_argument("--path", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="pop1 + refresh token only (no register)")
    ap.add_argument("--imap-list", action="store_true", help="after smoke, list recent subjects")
    args = ap.parse_args(argv)
    cfg: dict[str, Any] = {}
    if args.path:
        cfg["hotmail_pool_path"] = args.path
    st = status(cfg=cfg)
    if args.json and not args.smoke:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    print(f"[*] path={st['path']}")
    print(f"[*] unique={st['unique']} with_rt={st['with_refresh_token']} used={st['used_file_lines']} dead={st['dead_file_lines']}")
    if not args.smoke:
        print(f"[*] {st['note']}")
        return 0
    # smoke: peek first without permanent pop — read only first line then refresh
    pool = _pool_path(cfg)
    row = None
    for line in pool.read_text(encoding="utf-8", errors="replace").splitlines():
        row = parse_line(line)
        if row and row.get("refresh_token"):
            break
    if not row:
        print("[!] empty pool")
        return 1
    print(f"[*] smoke email={row['email']}")
    try:
        at = refresh_access_token(row, cfg)
        print(f"[+] token ok len={len(at)} client={row.get('_token_client_id','')[:12]}")
    except Exception as exc:
        print(f"[-] token fail: {exc}")
        return 2
    if args.imap_list:
        try:
            msgs = imap_fetch_recent(row["email"], at, limit=5)
            print(f"[+] imap msgs={len(msgs)}")
            for m in msgs[:5]:
                print("   -", (m.get("subject") or "")[:80])
        except Exception as exc:
            print(f"[-] imap fail: {exc}")
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
