# Claude 渠道恢复监视：倍佬/百佬任一恢复 → 切过去 → 补发真 Claude 交叉复核。
# v4：只做补审。GLM 兜底评审已由主流程直发（wrapper 自带故障转移）。
import json, os, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = r"D:/Users/grok-auto-register"
TOKEN = open(r"C:/Users/zhugu/.kimi-code/mcp-a2a-bridge/data/a2a_token", encoding="utf-8").read().strip()
SETTINGS_PATH = r"C:/Users/zhugu/.claude/settings.json"
REVIEW_OUT = ROOT + "/logs/_claude_egress_xreview.md"
PROMPT = open(ROOT + "/logs/_prompt_claude_egress_xreview2.txt", encoding="utf-8").read()


def get_channel(suffix):
    db = sqlite3.connect(r"C:/Users/zhugu/.cc-switch/cc-switch.db")
    for name, cfg in db.execute("SELECT name, settings_config FROM providers WHERE app_type='claude'"):
        env = json.loads(cfg).get("env", {})
        if env.get("ANTHROPIC_AUTH_TOKEN", "").endswith(suffix):
            return env.get("ANTHROPIC_BASE_URL", ""), env["ANTHROPIC_AUTH_TOKEN"]
    return None, None


def probe_token(suffix, model):
    url, tok = get_channel(suffix)
    if not url:
        return False
    body = json.dumps({"model": model, "max_tokens": 8,
                       "messages": [{"role": "user", "content": "OK"}]}).encode()
    req = urllib.request.Request(url + "/v1/messages", data=body, headers={
        "Content-Type": "application/json", "x-api-key": tok, "anthropic-version": "2023-06-01"})
    try:
        urllib.request.urlopen(req, timeout=40)
        return True
    except Exception:
        return False


def rpc(port, method, params, timeout=60):
    body = {"jsonrpc": "2.0", "id": "x", "method": method, "params": params}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def dispatch_and_wait(text, max_rounds=160):
    try:
        d = rpc(4942, "message/send", {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}}, 120)
    except Exception as e:
        return "dispatch_err", str(e)[:200]
    if "result" not in d:
        return "dispatch_err", json.dumps(d, ensure_ascii=False)[:200]
    tid = d["result"]["id"]
    for _ in range(max_rounds):
        time.sleep(15)
        try:
            t = rpc(4942, "tasks/get", {"id": tid}, 30).get("result", {})
        except Exception:
            continue
        st = t.get("status", {}).get("state")
        if st in ("completed", "failed", "canceled"):
            msg = t.get("status", {}).get("message", {})
            out = "\n".join(p.get("text", "") for p in msg.get("parts", []) if p.get("text"))
            return st, out
    return "timeout", ""


def switch_to(keyword):
    subprocess.run([sys.executable, ROOT + "/scripts/cc_rotate_claude_provider.py", "switch", keyword],
                   capture_output=True, timeout=60)
    # Write pin to prevent wrapper from immediately rotating away
    # Include token_suffix so wrapper can verify the pin matches the current channel
    try:
        settings = json.loads(open(SETTINGS_PATH, encoding="utf-8").read())
        cur_token = (settings.get("env", {}) or {}).get("ANTHROPIC_AUTH_TOKEN", "")
        pin = {
            "channel": keyword,
            "token_suffix": cur_token[-12:] if cur_token else "",
            "written_at": time.time(),
            "expires_at": time.time() + 600,  # 10 min TTL
            "ttl_s": 600,
        }
        pinfile = Path.home() / ".claude" / ".channel_pin.json"
        pinfile.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(pinfile) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pin, f, ensure_ascii=False)
        os.replace(tmp, str(pinfile))
    except Exception:
        pass


for rnd in range(80):  # ~4h
    beilao = probe_token("722663", "claude-opus-4-8")
    bailao = probe_token("648f3c", "claude-opus-4-6")
    print(f"[{time.strftime('%H:%M:%S')}] round {rnd} 倍佬={'OK' if beilao else 'BAD'} "
          f"百佬={'OK' if bailao else 'BAD'}", flush=True)
    if beilao or bailao:
        switch_to("倍佬" if beilao else "百佬")
        st, out = dispatch_and_wait(PROMPT)
        if st == "completed":
            open(REVIEW_OUT, "w", encoding="utf-8").write(
                "state: completed\nreviewer: Claude (恢复后补审)\n\n" + out)
            print("CLAUDE_REVIEW_DONE:", len(out), flush=True)
            sys.exit(0)
        print("attempt failed:", st, out[:120], flush=True)
        time.sleep(120)
        continue
    time.sleep(180)
print("GIVEUP: Claude 渠道 4h 未恢复", flush=True)
sys.exit(3)
