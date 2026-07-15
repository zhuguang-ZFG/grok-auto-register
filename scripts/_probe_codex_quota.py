# -*- coding: utf-8 -*-
"""探测 plus 号 codex/responses 配额（找出还有 codex 额度的号）。"""
import json
import urllib.request
import urllib.error
import uuid
import os
import sys

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
        return e.code, txt[:120]
    except Exception as e:
        return -1, type(e).__name__


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    accs = json.load(open(ACCS, encoding="utf-8"))
    plus = [a for a in accs if a.get("plan_type") == "plus" and a.get("status") == "正常"]
    plus.sort(key=lambda a: -a.get("text_success", 0))
    quota = []
    for a in plus[:n]:
        code, msg = probe(a["access_token"])
        tag = a["email"][:32]
        print(code, tag, msg.replace("\n", " ")[:100])
        if code == 200:
            quota.append(a["email"])
    print("quota-ok:", len(quota))


if __name__ == "__main__":
    main()
