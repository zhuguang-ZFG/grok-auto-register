#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /tmp/jf10 下 sub2api 导出的 20 个 json 转成 chatgpt2api accounts.json 账号形状，
并 merge 进网关数据源。死 workspace fc4f8db5 的号标禁用。"""
import json
import glob
import os
import shutil
import sys
import time

SRC = r"C:\Users\zhugu\AppData\Local\Temp\jf10"
POOL = "D:/Users/grok-auto-register/chatgpt_auths"
ACC = "D:/Users/grok-auto-register/chatgpt2api/data/accounts.json"
DEAD_WS = "fc4f8db5"


def parse_accounts(s):
    if isinstance(s, list):
        return s
    # sub2api 导出是 python repr：单引号 + True/False/None
    fixed = (s.replace("'", '"')
              .replace(": True", ": true").replace(": False", ": false")
              .replace(": None", ": null")
              .replace("[True", "[true").replace("[False", "[false"))
    return json.loads(fixed)


def main() -> None:
    files = sorted(glob.glob(os.path.join(SRC, "*.json")))
    accs = json.load(open(ACC, encoding="utf-8"))
    existing_emails = {a.get("email") for a in accs}
    pool_files = {f.lower() for f in os.listdir(POOL)}
    new_gate = 0
    new_pool = 0
    dup = 0
    dead = 0
    plans = {}
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        items = parse_accounts(d["accounts"])
        for it in items:
            cred = it.get("credentials", {})
            email = cred.get("email") or it.get("name")
            at = cred.get("access_token", "")
            wsid = cred.get("chatgpt_account_id", "")
            plan = cred.get("plan_type", "?")
            plans[plan] = plans.get(plan, 0) + 1
            if email in existing_emails:
                dup += 1
                continue
            is_dead = wsid.startswith(DEAD_WS)
            entry = {
                "created_at": int(time.time()),
                "access_token": at,
                "email": email,
                "user_id": cred.get("chatgpt_user_id", ""),
                "account_id": cred.get("account_id", wsid),
                "chatgpt_account_id": wsid,
                "type": "oauth",
                "plan_type": plan,
                "status": "禁用" if is_dead else "正常",
                "proxy": "",
                "refresh_token": cred.get("refresh_token", ""),
                "id_token": cred.get("id_token", ""),
                "text_success": 0,
                "text_fail": 0,
            }
            accs.append(entry)
            existing_emails.add(email)
            new_gate += 1
            if is_dead:
                dead += 1
            # 号池文件（sub2api 原始形状，便于审计）
            pfn = email.replace("@", "_at_").replace("+", "_") + ".json"
            if pfn.lower() not in pool_files:
                with open(os.path.join(POOL, pfn), "w", encoding="utf-8") as f:
                    json.dump(it, f, ensure_ascii=False, indent=1)
                new_pool += 1
    shutil.copy2(ACC, ACC + ".bak_jf10")
    json.dump(accs, open(ACC, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"files={len(files)} new_gate={new_gate} (dead_ws={dead}) new_pool={new_pool} dup={dup} plans={plans} total_gate={len(accs)}")


if __name__ == "__main__":
    sys.exit(main())
