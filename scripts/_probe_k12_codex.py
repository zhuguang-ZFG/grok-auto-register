# -*- coding: utf-8 -*-
"""探测 k12 号 codex/responses 可用性（区分 workspace 死/活 + codex 配额）。"""
import json
import collections
import urllib.request
import urllib.error
import uuid
import os

os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
ACCS = r"D:\Users\grok-auto-register\chatgpt2api\data\accounts.json"


def probe(at: str) -> tuple[int, str]:
    body = {
        "model": "gpt-5.5",
        "instructions": "You are Codex, a coding assistant.",
        "input": [{"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "say OK"}]}],
        "store": False, "stream": True,
    }
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/codex/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + at, "Content-Type": "application/json",
                 "originator": "codex_cli_rs", "OpenAI-Beta": "responses=experimental",
                 "session_id": str(uuid.uuid4()),
                 "User-Agent": "codex_cli_rs/0.55.0 (Windows 10.0.22631; x86_64)"},
        method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=90)
        r.read(100)
        return 200, "OK"
    except urllib.error.HTTPError as e:
        txt = e.read(200).decode("utf-8", "ignore")
        return e.code, txt[:100]
    except Exception as e:
        return -1, type(e).__name__


def main() -> None:
    accs = json.load(open(ACCS, encoding="utf-8"))
    k12 = [a for a in accs if a.get("plan_type") == "k12" and a.get("status") == "正常"]
    ws = collections.Counter(a.get("chatgpt_account_id", "?")[:8] for a in k12)
    print("k12 workspaces:", dict(ws))
    for a in k12:
        code, msg = probe(a["access_token"])
        print(code, a["email"][:34], a.get("chatgpt_account_id", "?")[:8], msg.replace("\n", " ")[:80])


if __name__ == "__main__":
    main()
