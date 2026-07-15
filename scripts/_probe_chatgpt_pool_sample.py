# -*- coding: utf-8 -*-
"""抽样探测 chatgpt 池账号存活（只读 AT check，不耗 RT）。"""
import json
import random
import sys
import collections

sys.path.insert(0, r"D:\Users\grok-auto-register")
from chatgpt_k12.token_check import check_account

PROXY = "http://127.0.0.1:7897"
accs = json.load(open(r"D:\Users\grok-auto-register\chatgpt2api\data\accounts.json", encoding="utf-8"))
alive_pool = [a for a in accs if a.get("status") == "正常" and a.get("access_token")]
plus = [a for a in alive_pool if a.get("plan_type") == "plus"]
k12 = [a for a in alive_pool if a.get("plan_type") == "k12"]
other = [a for a in alive_pool if a.get("plan_type") not in ("plus", "k12")]
print(f"pool: plus={len(plus)} k12={len(k12)} other={len(other)}")

random.seed(7)
sample = random.sample(plus, min(15, len(plus))) + random.sample(k12, min(15, len(k12))) + other[:2]
stat = collections.Counter()
for a in sample:
    tag = f"{a.get('plan_type')}:{a.get('email','?')[:30]}"
    try:
        r = check_account(a["access_token"], proxy_url=PROXY)
        stat[f"{a.get('plan_type')}_alive({r.get('plan_type')})"] += 1
        print("OK ", tag, "->", r.get("plan_type"))
    except Exception as e:
        msg = str(e)[:60]
        stat[f"{a.get('plan_type')}_dead"] += 1
        print("DEAD", tag, msg)
print(dict(stat))
