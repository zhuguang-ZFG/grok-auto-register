import json, base64, time, glob, os
from pathlib import Path

SRC = r"C:/Users/zhugu/AppData/Local/Temp/z05imp/sub2api-data-1784127324983.json"
DST = Path(r"D:/Users/grok-auto-register/cpa_auths")

def jwt_sub(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("sub", "")
    except Exception:
        return ""

HEADERS = {'x-grok-client-version': '0.2.93', 'x-xai-token-auth': 'xai-grok-cli', 'X-XAI-Token-Auth': 'xai-grok-cli', 'x-authenticateresponse': 'authenticate-response', 'x-grok-client-identifier': 'grok-shell', 'x-compaction-at': '400000', 'User-Agent': 'grok-shell/0.2.93 (linux; x86_64)'}

d = json.load(open(SRC, encoding="utf-8"))
accs = d["accounts"]

# existing identities in live pool
exist_email, exist_sub, exist_rt = set(), set(), set()
for f in DST.glob("xai-*.json"):
    try:
        j = json.load(open(f, encoding="utf-8"))
        if j.get("email"): exist_email.add(j["email"].lower())
        if j.get("sub"): exist_sub.add(j["sub"])
        if j.get("refresh_token"): exist_rt.add(j["refresh_token"])
    except Exception:
        pass

now = time.time()
imported, dup, bad = 0, 0, []
for a in accs:
    c = a.get("credentials", {})
    email = (c.get("email") or a.get("name") or "").strip().lower()
    at, rt = c.get("access_token", ""), c.get("refresh_token", "")
    if not at or not rt:
        bad.append((email or a.get("name"), "missing-token")); continue
    sub = jwt_sub(at) or (a.get("extra") or {}).get("local_account_id", "")
    if email in exist_email or (sub and sub in exist_sub) or rt in exist_rt:
        dup += 1; continue
    exp_ts = a.get("expires_at") or 0
    try:
        exp_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(float(exp_ts)))
        expires_in = max(0, int(float(exp_ts) - now))
    except Exception:
        exp_iso, expires_in = "", 21600
    rec = {
        "type": "xai",
        "access_token": at,
        "refresh_token": rt,
        "id_token": "",
        "token_type": "Bearer",
        "expires_in": expires_in,
        "expired": exp_iso,
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(now)),
        "sub": sub,
        "base_url": "https://cli-chat-proxy.grok.com/v1",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "auth_kind": "oauth",
        "headers": HEADERS,
        "email": email,
    }
    fname = f"xai-{email if email else sub}.json"
    (DST / fname).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    exist_email.add(email); exist_sub.add(sub); exist_rt.add(rt)
    imported += 1

print(f"accounts={len(accs)} imported={imported} dup={dup} bad={len(bad)}")
for b in bad[:10]: print(" BAD:", b)
